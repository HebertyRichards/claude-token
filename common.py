"""Shared constants, pricing, JSON/state helpers and formatting."""
import json
import os
from datetime import datetime, timezone

HOME = os.path.expanduser("~")
CLAUDE_DIR = os.path.join(HOME, ".claude")
SETTINGS = os.path.join(CLAUDE_DIR, "settings.json")
STATE = os.path.join(CLAUDE_DIR, "claude-token-state.json")
PROJECTS = os.path.join(CLAUDE_DIR, "projects")
MARKER = "claude-token"
# Path of the executable entry script (what settings.json commands point to).
ENTRY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "claude-token")
CONTEXT_WINDOW = 200_000
GUARD_ENV = "CLAUDE_TOKEN_HOOK"  # prevents the rewriter from re-triggering itself

# Estimated USD per million tokens: (input, output, cache_read, cache_write)
PRICES = {
    "fable": (5.0, 25.0, 0.50, 6.25), "mythos": (5.0, 25.0, 0.50, 6.25),
    "opus": (5.0, 25.0, 0.50, 6.25), "sonnet": (3.0, 15.0, 0.30, 3.75),
    "haiku": (1.0, 5.0, 0.10, 1.25),
}


def price_for(model):
    m = (model or "").lower()
    for key, p in PRICES.items():
        if key in m:
            return p
    return PRICES["sonnet"]


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
