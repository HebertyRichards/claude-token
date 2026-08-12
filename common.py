"""Shared constants, pricing, JSON/state helpers and formatting."""
import json
import os
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
CONTEXT_WINDOW = 200_000
GUARD_ENV = "CLAUDE_TOKEN_HOOK"  # prevents the rewriter from re-triggering itself
STALE_DAYS = 90  # after this, the price table is reported as unverified

# Seed prices, USD per million tokens: (input, output, cache_read, cache_write).
# Cache read is 0.1x input, cache write 1.25x (the 5-minute TTL; a 1h-TTL write
# is 2x, which these numbers do NOT model). Keys are matched as substrings of
# the model id, longest key first — so "sonnet-5" wins over "sonnet".
# There is no pricing endpoint on the API, so this table cannot self-update:
# it is written to PRICES_FILE on first use for you to edit.
SEED_PRICES = {
    "fable": (10.0, 50.0, 1.00, 12.50), "mythos": (10.0, 50.0, 1.00, 12.50),
    "opus": (5.0, 25.0, 0.50, 6.25),
    "sonnet-5": (2.0, 10.0, 0.20, 2.50),   # introductory, reverts to 3/15
    "sonnet": (3.0, 15.0, 0.30, 3.75),
    "haiku": (1.0, 5.0, 0.10, 1.25),
}
SEED_CHECKED = "2026-08-11"
SEED_NOTES = (
    "Edit the prices below when Anthropic's change; there is no API to fetch "
    "them. Order: input, output, cache_read, cache_write (USD per million "
    "tokens). Keys match as substrings of the model id, longest first. "
    "sonnet-5 is at introductory pricing through 2026-08-31 and reverts to "
    "[3.0, 15.0, 0.30, 3.75] after that. Bump _checked when you verify."
)

_unknown = set()   # model ids seen with no price entry


def load_prices():
    """Price table from PRICES_FILE, seeded on first use."""
    data = load_json(PRICES_FILE, None)
    if not isinstance(data, dict) or not data.get("prices"):
        data = {"_notes": SEED_NOTES, "_checked": SEED_CHECKED,
                "prices": {k: list(v) for k, v in SEED_PRICES.items()}}
        try:
            save_json(PRICES_FILE, data)
        except OSError:
            pass
    return data


def price_for(model):
    """(input, output, cache_read, cache_write) or None if the model is
    unknown. Never guesses: an unpriced model must be reported, not silently
    billed at some other model's rate."""
    m = (model or "").lower()
    prices = load_prices().get("prices", {})
    for key in sorted(prices, key=len, reverse=True):
        if key in m:
            p = prices[key]
            if isinstance(p, list) and len(p) == 4:
                return tuple(float(x) for x in p)
    if m and m != "<synthetic>":
        _unknown.add(m)
    return None


def price_warnings():
    """Lines to show the user about price-table trust. Empty when all is well."""
    out = []
    checked = parse_ts((load_prices().get("_checked") or "") + "T00:00:00+00:00")
    if checked is None:
        out.append(f"[!] {PRICES_FILE}: _checked ausente — custos não verificados")
    else:
        age = (datetime.now(timezone.utc) - checked).days
        if age > STALE_DAYS:
            out.append(f"[!] tabela de preços verificada há {age} dias — "
                       f"confira {PRICES_FILE}")
    if _unknown:
        out.append(f"[!] sem preço cadastrado para {', '.join(sorted(_unknown))}"
                   f" — esses turnos ficaram FORA do custo. Adicione em {PRICES_FILE}")
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


def level():
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
