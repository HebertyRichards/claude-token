"""Shared constants, pricing, JSON/state helpers and formatting."""
import json
import os
import re
import sys
from datetime import datetime, timezone

HOME = os.path.expanduser("~")
CLAUDE_DIR = os.path.join(HOME, ".claude")
SETTINGS = os.path.join(CLAUDE_DIR, "settings.json")
STATE = os.path.join(CLAUDE_DIR, "claude-token-state.json")
PRICES_FILE = os.path.join(CLAUDE_DIR, "claude-token-prices.json")
PROJECTS = os.path.join(CLAUDE_DIR, "projects")
MARKER = "claude-token"
# Path of the executable entry script (what settings.json commands point to).
ENTRY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "claude-token")
# Command line that settings.json and the PATH shim run. On Windows the shebang
# is useless (python3 is the Store stub) and Git Bash eats backslashes, so call
# this interpreter explicitly with forward-slash paths.
if os.name == "nt":
    COMMAND = " ".join(f'"{p}"' if " " in p else p for p in
                       (sys.executable.replace("\\", "/"), ENTRY.replace("\\", "/")))
else:
    COMMAND = ENTRY
CONTEXT_WINDOW = 200_000  # fallback only: see window_for()
WINDOW_FILE = os.path.join(CLAUDE_DIR, "claude-token-window.json")
GUARD_ENV = "CLAUDE_TOKEN_HOOK"  # prevents the rewriter from re-triggering itself
STALE_DAYS = 90  # after this, the price table is reported as unverified

# Seed prices, USD per million tokens: (input, output, cache_read, cache_write).
# cache_write is the 5-minute-TTL rate (1.25x input). A 1-hour-TTL write is
# WRITE_1H x input on every model, so it needs no column: turn_cost() applies
# it to the tokens the transcript marks as 1h. Cache read is NOT a fixed share
# of input any more: 0.1x on most models, 0.05x on Opus/Sonnet 5.5, 0.025x on
# Fable/Mythos 5.1 — so every row spells it out.
#
# One row per model VERSION, and a row prices only its own version: "opus-5"
# is Opus 5 (claude-opus-5, claude-opus-5-20260301) and never Opus 5.5 — see
# price_row(). There are no family-wide rows: an "opus" row pricing every Opus
# is how 5.5 came to be billed at 5.0 rates. A version with no row of its own
# is reported as unpriced.
# There is no pricing endpoint on the API, so this table cannot self-update:
# it is written to PRICES_FILE on first use for you to edit.
SEED_PRICES = {
    "fable-5-1": (10.0, 50.0, 0.25, 12.50), "mythos-5-1": (10.0, 50.0, 0.25, 12.50),
    "fable-5": (10.0, 50.0, 1.00, 12.50), "mythos-5": (10.0, 50.0, 1.00, 12.50),
    "opus-5-5": (4.0, 20.0, 0.20, 5.00),
    "opus-5": (5.0, 25.0, 0.50, 6.25),
    "opus-4-8": (5.0, 25.0, 0.50, 6.25), "opus-4-7": (5.0, 25.0, 0.50, 6.25),
    "opus-4-6": (5.0, 25.0, 0.50, 6.25), "opus-4-5": (5.0, 25.0, 0.50, 6.25),
    "opus-4-1": (15.0, 75.0, 1.50, 18.75), "opus-4": (15.0, 75.0, 1.50, 18.75),
    "sonnet-5-5": (2.0, 10.0, 0.10, 2.50),
    "sonnet-5": (2.0, 10.0, 0.20, 2.50),
    "sonnet-4-6": (3.0, 15.0, 0.30, 3.75), "sonnet-4-5": (3.0, 15.0, 0.30, 3.75),
    "sonnet-4": (3.0, 15.0, 0.30, 3.75),
    # Haiku 5.5 is priced by prompt size: see BIG_PROMPT.
    "haiku-5-5": (0.10, 0.50, 0.01, 0.125),
    "haiku-5-5" + ">100k": (0.50, 2.50, 0.05, 0.625),
    "haiku-4-5": (1.0, 5.0, 0.10, 1.25),
    "3-5-haiku": (0.80, 4.0, 0.08, 1.00),   # the older naming: claude-3-5-haiku-…
}
# Rows earlier seeds wrote that no longer exist: the family-wide ones, and a
# workaround key. Dropped from an existing file while still exactly as seeded;
# one the user edited is theirs and stays (it no longer prices any version).
RETIRED = {
    "fable": (10.0, 50.0, 1.00, 12.50), "mythos": (10.0, 50.0, 1.00, 12.50),
    "opus": (5.0, 25.0, 0.50, 6.25), "sonnet": (3.0, 15.0, 0.30, 3.75),
    "haiku": (1.0, 5.0, 0.10, 1.25), "opus-4-20": (15.0, 75.0, 1.50, 18.75),
}
# A key ending in BIG_SUFFIX is the rate for the same model when one request's
# prompt (input + cache read + cache write) passes BIG_PROMPT tokens. It can
# never match a model id by itself, so it only applies through price_for().
BIG_PROMPT = 100_000
BIG_SUFFIX = ">100k"
SEED_CHECKED = "2026-10-07"
SEED_NOTES = (
    "Edit the prices below when Anthropic's change; there is no API to fetch "
    "them. Order: input, output, cache_read, cache_write (USD per million "
    "tokens). One row per model version, and a row prices only its own "
    "version: 'opus-5' prices claude-opus-5 and claude-opus-5-20260301, never "
    "claude-opus-5-5. A model with no row of its own is reported as unpriced "
    "instead of being billed at another version's rate: add its row. A key "
    "ending in '>100k' is the rate for that model when a single prompt passes "
    "100,000 tokens (Haiku 5.5). cache_write is the 5-minute rate; 1-hour "
    "writes are billed at 2x input automatically, from what the transcript "
    "records. Bump _checked when you verify."
)
# Which TTL a session gets is the harness's call (a subscription writes 1h,
# and may fall back to 5m), so it is read per response, never assumed.
WRITE_1H = 2.0
TTL_5M, TTL_1H = 300, 3600

_unknown = set()   # model ids seen with no price entry


_prices = None     # per-process cache: every caller is a short-lived command


def load_prices():
    """Price table from PRICES_FILE: seeded on first use, and topped up with
    any row the seed has gained since.

    Without the top-up a new model keeps matching an older, more generic row
    on every install that predates it — "claude-opus-5-5" billed by "opus",
    at Opus 5 rates — because the file used to be written exactly once. Rows
    already in the file are the user's and are never overwritten;
    price_warnings() reports the ones that disagree with a newer seed."""
    global _prices
    if _prices is not None:
        return _prices
    data = load_json(PRICES_FILE, None)
    if (not isinstance(data, dict) or not isinstance(data.get("prices"), dict)
            or not data["prices"]):
        data = {"_notes": SEED_NOTES, "_checked": SEED_CHECKED,
                "prices": {k: list(v) for k, v in SEED_PRICES.items()}}
        changed = True
    else:
        missing = [k for k in SEED_PRICES if k not in data["prices"]]
        for k in missing:
            data["prices"][k] = list(SEED_PRICES[k])
        retired = [k for k, old in RETIRED.items()
                   if k in data["prices"] and _same_row(data["prices"][k], old)]
        for k in retired:
            del data["prices"][k]
        changed = bool(missing or retired)
        if changed:
            data["_notes"] = SEED_NOTES
            # Every seed row is now in the file exactly as the seed has it:
            # the table is as verified as the seed. An edited row keeps the
            # file's older date, so it stays reported.
            if not seed_diffs(data) and (data.get("_checked") or "") < SEED_CHECKED:
                data["_checked"] = SEED_CHECKED
    if changed:
        try:
            save_json(PRICES_FILE, data)
        except OSError:
            pass
    _prices = data
    return data


def _same_row(row, seed):
    try:
        return [float(x) for x in row] == [float(x) for x in seed]
    except (TypeError, ValueError):
        return False


def seed_diffs(data):
    """Seed rows whose value in the file differs: [(key, in file, in seed)]."""
    out = []
    for key, seed in SEED_PRICES.items():
        row = (data.get("prices") or {}).get(key)
        if not _same_row(row, seed):
            out.append((key, row, list(seed)))
    return out


# Right after a key, "-5" or "-12" means the id names a LATER version than the
# key does; "-20260301" (a date), "-v1" or "[1m]" does not.
_LATER_VERSION = re.compile(r"-\d{1,2}(?!\d)")


def price_row(model):
    """The key of the row that prices `model`, or None.

    A row prices exactly its own version: "opus-5" matches claude-opus-5 and
    claude-opus-5-20260301 but not claude-opus-5-5. Plain substring matching
    let a new version fall through to the nearest older or family-wide row and
    be billed at its rate without a word — in the main thread and in every
    subagent alike, since both are priced here."""
    for key in sorted(load_prices().get("prices", {}), key=len, reverse=True):
        if not key.endswith(BIG_SUFFIX) and same_version(model, key):
            return key
    return None


def same_version(model, name):
    """Does the id `model` name exactly the version `name` does? True for
    ("claude-opus-5-20260301", "opus-5"), false for ("claude-opus-5-5",
    "opus-5")."""
    m = (model or "").lower()
    at = m.find(name.lower())
    return at >= 0 and not _LATER_VERSION.match(m, at + len(name))


def price_for(model, prompt_tokens=0):
    """(input, output, cache_read, cache_write) or None if the model is
    unknown. Never guesses: an unpriced model must be reported, not silently
    billed at some other model's rate.

    `prompt_tokens` is the size of that one request's prompt; it only matters
    for models that have a BIG_SUFFIX row."""
    m = (model or "").lower()
    prices = load_prices().get("prices", {})
    key = price_row(model)
    if key:
        p = prices[key]
        if prompt_tokens > BIG_PROMPT:
            p = prices.get(key + BIG_SUFFIX, p)
        if isinstance(p, list) and len(p) == 4:
            return tuple(float(x) for x in p)
    if m and m != "<synthetic>":
        _unknown.add(m)
    return None


def cache_writes(u):
    """(5-minute, 1-hour) cache-write tokens of one response. Old transcripts
    carry only the total; that counts as 5m, the rate the table lists."""
    total = u.get("cache_creation_input_tokens", 0)
    split = u.get("cache_creation")
    h1 = split.get("ephemeral_1h_input_tokens", 0) if isinstance(split, dict) else 0
    return max(0, total - h1), h1


def turn_cost(model, u):
    """Estimated USD of one response, or None when the model is unpriced."""
    if model == "<synthetic>":
        # A turn the harness wrote itself (an interrupted request): no model
        # ran, so it costs nothing — and is not a model missing from the table.
        return 0.0
    inp, cr = u.get("input_tokens", 0), u.get("cache_read_input_tokens", 0)
    w5, w1 = cache_writes(u)
    p = price_for(model, inp + cr + w5 + w1)
    if not p:
        return None
    pi, po, pcr, pcw = p
    return (inp * pi + u.get("output_tokens", 0) * po + cr * pcr
            + w5 * pcw + w1 * pi * WRITE_1H) / 1e6


def cache_ttl(usages):
    """Seconds the prompt cache lives, from the latest response that wrote
    one. No write seen yet: the short TTL, so nothing is promised as warm."""
    for u in reversed(usages):
        w5, w1 = cache_writes(u)
        if w5 or w1:
            return TTL_1H if w1 >= w5 else TTL_5M
    return TTL_5M


def fetch_plan_usage(timeout=3):
    """The account's plan windows (5h, week, per-model), from the endpoint
    /usage itself reads — or None: no subscription login, offline, expired
    token. The statusline's stdin carries the two main windows, but only
    inside a live session; this is the one source that works outside one."""
    creds = load_json(os.path.join(CLAUDE_DIR, ".credentials.json"), {})
    token = ((creds.get("claudeAiOauth") or {}).get("accessToken")
             if isinstance(creds, dict) else None)
    if not token:
        return None
    # Imported here: the hook loads this module on every prompt and never
    # needs the network stack.
    import urllib.request
    req = urllib.request.Request(
        "https://api.anthropic.com/api/oauth/usage",
        headers={"Authorization": f"Bearer {token}",
                 "anthropic-beta": "oauth-2025-04-20"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.load(resp)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def remember_window(session_id, size):
    """Statusline side: keep the context window the harness reports for this
    session, so the hook (whose input does not carry it) can use it too."""
    seen = load_json(WINDOW_FILE, {})
    if not session_id or not isinstance(seen, dict) or seen.get(session_id) == size:
        return
    seen[session_id] = size
    try:
        save_json(WINDOW_FILE, dict(list(seen.items())[-50:]))
    except OSError:
        pass


def window_for(session_id):
    """Context window of a session: what the harness reported, else the
    conservative default. A fixed 200k calls a 1M-window session "70% full"
    at 140k and nudges a /compact nobody needs."""
    seen = load_json(WINDOW_FILE, {})
    size = seen.get(session_id) if isinstance(seen, dict) else None
    return size if isinstance(size, int) and size > 0 else CONTEXT_WINDOW


def price_warnings():
    """Lines to show the user about price-table trust. Empty when all is well."""
    out = []
    data = load_prices()
    checked = parse_ts((data.get("_checked") or "") + "T00:00:00+00:00")
    # The repo verified its table after this file was last checked: a row that
    # disagrees is either a deliberate edit or a price that changed since.
    if (data.get("_checked") or "") < SEED_CHECKED:
        for key, row, seed in seed_diffs(data):
            out.append(f"[!] preço de '{key}' difere da tabela do repo, conferida "
                       f"em {SEED_CHECKED} (arquivo {row}, repo {seed}) — ajuste "
                       f"em {PRICES_FILE}, ou atualize _checked se for intencional")
    if checked is None:
        out.append(f"[!] {PRICES_FILE}: _checked ausente — custos não verificados")
    else:
        age = (datetime.now(timezone.utc) - checked).days
        if age > STALE_DAYS:
            out.append(f"[!] tabela de preços verificada há {age} dias — "
                       f"confira {PRICES_FILE}")
    if _unknown:
        out.append(f"[!] sem preço cadastrado para {', '.join(sorted(_unknown))}"
                   f" — esses turnos ficaram FORA do custo. Cada versão tem a "
                   f"própria linha (nenhuma herda o preço de outra): adicione "
                   f"em {PRICES_FILE}")
    return out


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


def save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_ts(ts):
    try:
        return datetime.fromisoformat((ts or "").replace("Z", "+00:00"))
    except ValueError:
        return None


def fmt_k(n):
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    return f"{n/1000:.1f}k" if n >= 1000 else str(int(n))


# --- ON/OFF state (periods recorded in ~/.claude/claude-token-state.json) ---

def load_state():
    return load_json(STATE, {"periods": []})


def periods():
    return load_state().get("periods", [])


def is_on():
    ps = periods()
    return bool(ps) and ps[-1].get("end") is None


# --- Optimizer level -------------------------------------------------------
# How much the harness injects. There used to be a level 3 that had Haiku
# rewrite every prompt; it was removed — it cost ~17.7k input tokens per
# prompt and altered the user's request on the way through. Every level here
# is text injection only: no model call, no rewriting.
LEVELS = {
    0: "off — nada injetado",
    1: "lean — regras de saída/escopo + aviso de resultado gigante + memória de compact",
    2: "normal — + RAG e aviso de contexto cheio (padrão)",
}
DEFAULT_LEVEL = 2
# Per-process override of the saved level: `CLAUDE_TOKEN_LEVEL=0 claude` runs
# one session with nothing injected, without touching settings or the state
# other sessions read. The benchmark's two arms are told apart this way.
LEVEL_ENV = "CLAUDE_TOKEN_LEVEL"


def level():
    try:
        forced = int(os.environ.get(LEVEL_ENV, ""))
        if forced in LEVELS:
            return forced
    except ValueError:
        pass
    try:
        n = int(load_state().get("level", DEFAULT_LEVEL))
    except (TypeError, ValueError):
        return DEFAULT_LEVEL
    # A level 3 left in state from the old rewrite era degrades to the top
    # level that still exists, rather than silently falling back to default.
    return n if n in LEVELS else (max(LEVELS) if isinstance(n, int) and n > max(LEVELS)
                                  else DEFAULT_LEVEL)


def set_level(n):
    st = load_state()
    st["level"] = n
    save_json(STATE, st)
