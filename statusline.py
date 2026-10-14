"""Statusline: short lines (fit narrow terminals) — context fill, cache
state, the plan window and the session's measured totals."""
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

from common import (CLAUDE_DIR, CONTEXT_WINDOW, TTL_1H, WRITE_1H, cache_ttl,
                    fetch_plan_usage, fmt_k, level, load_json, load_state,
                    parse_ts, price_for, remember_window, save_json, turn_cost)
from usage import segment_usage, subagent_usage


WEEKDAYS = ("seg", "ter", "qua", "qui", "sex", "sáb", "dom")
USAGE_CACHE = os.path.join(CLAUDE_DIR, "claude-token-usage-cache.json")
USAGE_TTL = 120  # segundos entre consultas ao endpoint (statusline roda direto)
COLD_ADVICE = 30_000  # tokens: acima disso, cache frio pede /compact ou conversa nova


def _dot(pct):
    return "🟢" if pct < 60 else "🟡" if pct < 85 else "🔴"


def _fmt_window(n):
    """200000 -> 200k, 1000000 -> 1M: a window is a round number, so it reads
    as one (fmt_k would print 200.0k and 1.0M)."""
    if n >= 1_000_000 and n % 1_000_000 == 0:
        return f"{n // 1_000_000}M"
    return f"{n // 1000}k" if n % 1000 == 0 else fmt_k(n)


def _fmt_reset(dt, with_day):
    local = dt.astimezone()
    hhmm = local.strftime("%H:%M")
    return f"{WEEKDAYS[local.weekday()]} {hhmm}" if with_day else hhmm


def scoped_weekly():
    """Janelas semanais por modelo (ex.: Fable) — o stdin do statusline não
    traz esse recorte; ele só existe no endpoint OAuth que o /usage consulta.
    O resultado fica em cache (USAGE_TTL) e falha de rede/token mantém o
    último valor conhecido, sem travar a statusline."""
    cache = load_json(USAGE_CACHE, {})
    entries = cache.get("entries", [])
    if time.time() - cache.get("fetched_at", 0) < USAGE_TTL:
        return entries
    data = fetch_plan_usage()
    if data is not None:  # sem rede/token expirado: segue com o cache antigo
        entries = [
            {"label": model.lower(), "percent": lim.get("percent", 0),
             "resets_at": lim.get("resets_at")}
            for lim in data.get("limits") or []
            if isinstance(lim, dict) and lim.get("kind") == "weekly_scoped"
            and (model := ((lim.get("scope") or {}).get("model") or {})
                 .get("display_name"))
        ]
    try:
        # fetched_at avança mesmo em falha para não repetir o timeout a cada refresh.
        save_json(USAGE_CACHE, {"fetched_at": time.time(), "entries": entries})
    except OSError:
        pass
    return entries


def plan_usage_line(rate_limits):
    # Mesmos números do /usage: 5h e semana vêm no stdin (só para assinantes,
    # após a primeira resposta da sessão; cada janela pode faltar), e as
    # janelas por modelo (Fable) vêm de scoped_weekly().
    parts = []
    for key, label in (("five_hour", "5h"), ("seven_day", "semana")):
        window = rate_limits.get(key) or {}
        pct = window.get("used_percentage")
        if pct is None:
            continue
        piece = f"{label} {pct:.0f}% {_dot(pct)}"
        resets = window.get("resets_at")
        if resets:
            when = _fmt_reset(datetime.fromtimestamp(resets, tz=timezone.utc),
                              with_day=key != "five_hour")
            piece += f" (reseta {when})"
        parts.append(piece)
    for entry in scoped_weekly():
        pct = entry.get("percent") or 0
        piece = f"{entry.get('label', '?')} {pct:.0f}% {_dot(pct)}"
        dt = parse_ts(entry.get("resets_at"))
        if dt:
            piece += f" (reseta {_fmt_reset(dt, with_day=True)})"
        parts.append(piece)
    return "uso do plano: " + " · ".join(parts) if parts else ""


def cmd_statusline():
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        data = {}
    model = (data.get("model") or {}).get("display_name", "?")
    transcript = data.get("transcript_path", "")
    # Only what came after the last /compact counts: the boundary resets
    # everything here, and /clear starts a new transcript file anyway.
    entries, carried = segment_usage(transcript) if transcript else ([], 0)
    u = entries[-1][2] if entries else {}
    ctx = (u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0)
           + u.get("cache_creation_input_tokens", 0)) or carried
    # The window is per model and plan (200k or 1M); the harness says which.
    window = (data.get("context_window") or {}).get("context_window_size")
    if isinstance(window, int) and window > 0:
        remember_window(data.get("session_id"), window)
    else:
        window = CONTEXT_WINDOW
    pct = ctx / window * 100
    dot ="🟢" if pct < 60 else "🟡" if pct < 85 else "🔴"
    # Warm cache = last turn mostly reused cached input (2.5-10% of the price);
    # cold = it re-wrote the cache (idle past the TTL, fresh session or
    # post-compact).
    warm = u.get("cache_read_input_tokens", 0) > u.get("cache_creation_input_tokens", 0)
    # Past the TTL the next turn rewrites the cache no matter how warm the
    # last one was — flip to cold by wall clock. The TTL is the one this
    # session is actually getting (1h or 5min), read from its own writes.
    last = parse_ts(entries[-1][0]) if entries else None
    ttl = cache_ttl([e[2] for e in entries])
    expired = bool(last) and (datetime.now(timezone.utc) - last).total_seconds() > ttl
    if expired:
        warm = False
    cache = "cache🔥" if warm else "cache🧊"
    if expired and ctx:
        # What the expiry costs: the next turn writes the whole context to
        # the cache again, at the write rate — the dearest there is. Shown
        # before the user types, which is the only moment it can be avoided.
        cache += f" reler {fmt_k(ctx)}"
        p = price_for(entries[-1][1], ctx)
        if p:
            write = p[0] * WRITE_1H if ttl == TTL_1H else p[3]
            cache += f" ≈${ctx * write / 1e6:.2f}"
        if ctx >= COLD_ADVICE:
            # A big context is cheaper to summarize or leave behind than to
            # rewrite and then drag through every later turn.
            cache += " → /compact ou conversa nova"
    elif warm and last:
        # Until when the next turn is still a cheap cache read: a cache that
        # lasts an hour is only worth having if it is used before it goes.
        cache += f" até {(last + timedelta(seconds=ttl)).astimezone():%H:%M}"

    st = load_state()
    ps = st.get("periods", [])
    on = bool(ps) and ps[-1].get("end") is None
    lv = level()
    eco = f"ECO✅ nv{lv} (/token level N)" if on else "ECO❌ OFF (/token on liga)"

    # Segment totals: responses, tokens read (input+cache), written (output)
    # and estimated cost — all restart from zero after a /compact.
    # These are direct measurements. There is deliberately no "without ECO"
    # column: the original prompt is never run, so any such number would be a
    # guess dressed as a measurement.
    turns = out_tok = read_tok = unpriced = 0
    cost = 0.0
    for _ts, mdl, usage in entries:
        turns += 1
        out_tok += usage.get("output_tokens", 0)
        read_tok += (usage.get("input_tokens", 0)
                     + usage.get("cache_read_input_tokens", 0)
                     + usage.get("cache_creation_input_tokens", 0))
        c = turn_cost(mdl, usage)
        if c is None:
            unpriced += 1
        else:
            cost += c

    # Subagents spawned in this segment: billed to the same plan window, but
    # their turns never enter this transcript — shown apart, never mixed into
    # the session's own numbers.
    since = entries[0][0] if entries else ""
    agents = 0
    sub_cost = 0.0
    for agent in subagent_usage(transcript) if transcript else []:
        mine = [e for e in agent if e[0] >= since]
        if not mine:
            continue
        agents += 1
        for _ts, mdl, usage in mine:
            c = turn_cost(mdl, usage)
            if c is None:
                unpriced += 1
            else:
                sub_cost += c

    print(f"{eco} | {model} | ctx {fmt_k(ctx)}/{_fmt_window(window)} ({pct:.0f}%) {dot} | {cache}")
    # The plan window is the headline: on a subscription it, not the dollar
    # estimate, is what actually runs out.
    usage = plan_usage_line(data.get("rate_limits") or {})
    if usage:
        print(usage)
    tail = f" · {agents} subagente(s) ~${sub_cost:.2f}" if agents else ""
    if unpriced:
        tail += f" · {unpriced} turno(s) sem preço"
    print(f"sessão ({turns} resp): leitura {fmt_k(read_tok)} · saída "
          f"{fmt_k(out_tok)} · custo ~${cost:.2f}{tail}")
