"""Statusline: short lines (fit narrow terminals) — context fill, session
totals, and ONE clear money comparison against the no-ECO baseline."""
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone

from common import (CLAUDE_DIR, CONTEXT_WINDOW, fmt_k, level, load_json,
                    load_state, parse_ts, price_for, save_json)
from usage import segment_usage


WEEKDAYS = ("seg", "ter", "qua", "qui", "sex", "sáb", "dom")
USAGE_CACHE = os.path.join(CLAUDE_DIR, "claude-token-usage-cache.json")
USAGE_TTL = 120  # segundos entre consultas ao endpoint (statusline roda direto)


def _dot(pct):
    return "🟢" if pct < 60 else "🟡" if pct < 85 else "🔴"


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
    try:
        creds = load_json(os.path.join(CLAUDE_DIR, ".credentials.json"), {})
        token = (creds.get("claudeAiOauth") or {}).get("accessToken")
        if token:
            req = urllib.request.Request(
                "https://api.anthropic.com/api/oauth/usage",
                headers={"Authorization": f"Bearer {token}",
                         "anthropic-beta": "oauth-2025-04-20"})
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = json.load(resp)
            entries = [
                {"label": model.lower(), "percent": lim.get("percent", 0),
                 "resets_at": lim.get("resets_at")}
                for lim in data.get("limits") or []
                if lim.get("kind") == "weekly_scoped"
                and (model := ((lim.get("scope") or {}).get("model") or {})
                     .get("display_name"))
            ]
    except Exception:
        pass  # sem rede/token expirado: segue com o cache antigo
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
    pct = ctx / CONTEXT_WINDOW * 100
    dot = "🟢" if pct < 60 else "🟡" if pct < 85 else "🔴"
    # Warm cache = last turn mostly reused cached input (10% of the price);
    # cold = it re-wrote the cache (idle >5min, fresh session or post-compact).
    warm = u.get("cache_read_input_tokens", 0) > u.get("cache_creation_input_tokens", 0)
    # The cache TTL is 5 min: past it, the next turn rewrites the cache no
    # matter how warm the last one was — flip to cold by wall clock.
    last = parse_ts(entries[-1][0]) if entries else None
    if last and (datetime.now(timezone.utc) - last).total_seconds() > 300:
        warm = False
    cache = "cache🔥" if warm else "cache🧊"

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
        inp = usage.get("input_tokens", 0)
        out = usage.get("output_tokens", 0)
        cr = usage.get("cache_read_input_tokens", 0)
        cw = usage.get("cache_creation_input_tokens", 0)
        out_tok += out
        read_tok += inp + cr + cw
        p = price_for(mdl)
        if p:
            pi, po, pcr, pcw = p
            cost += (inp * pi + out * po + cr * pcr + cw * pcw) / 1e6
        else:
            unpriced += 1

    print(f"{eco} | {model} | ctx {fmt_k(ctx)}/{CONTEXT_WINDOW//1000}k ({pct:.0f}%) {dot} | {cache}")
    # The plan window is the headline: on a subscription it, not the dollar
    # estimate, is what actually runs out.
    usage = plan_usage_line(data.get("rate_limits") or {})
    if usage:
        print(usage)
    tail = f" · {unpriced} turno(s) sem preço" if unpriced else ""
    print(f"sessão ({turns} resp): leitura {fmt_k(read_tok)} · saída "
          f"{fmt_k(out_tok)} · custo ~${cost:.2f}{tail}")
