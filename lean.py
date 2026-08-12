"""Lean-output layer: once-per-session rules scoped to the project's real
stack (a TS frontend gets vitest/playwright hints, not pytest), plus a
PostToolUse watcher that flags oversized tool results — language-agnostic by
design: it measures bytes, not runners."""
import json
import os
import sys

from common import CLAUDE_DIR, level, load_json, save_json

SESSIONS = os.path.join(CLAUDE_DIR, "claude-token-sessions.json")
BIG_RESULT = 20_000   # chars (~5k tokens): a tool result this big gets flagged
TEST_RUNNERS = ("pytest", "vitest", "jest", "playwright", "cypress", "mocha",
                "go test", "cargo test", "phpunit", "rspec", "npm test",
                "dotnet test", "mvn ", "gradle")


def test_hints(cwd):
    """Quiet-mode hints only for the runners this project actually has."""
    hints = []
    pkg = ""
    try:
        with open(os.path.join(cwd, "package.json"), encoding="utf-8") as f:
            pkg = f.read()
    except OSError:
        pass
    for marker, hint in (("vitest", "vitest run --reporter=dot"),
                         ("jest", "jest --silent"),
                         ("playwright", "playwright test --reporter=line"),
                         ("cypress", "cypress run --quiet"),
                         ("mocha", "mocha --reporter dot")):
        if marker in pkg:
            hints.append(hint)
    if any(os.path.exists(os.path.join(cwd, m)) for m in
           ("pytest.ini", "pyproject.toml", "setup.py", "requirements.txt")):
        hints.append("pytest -q --tb=short")
    for marker, hint in (("go.mod", "go test ./... (never -v)"),
                         ("Cargo.toml", "cargo test -q"),
                         ("pom.xml", "mvn -q test"),
                         ("composer.json", "phpunit --no-progress"),
                         ("Gemfile", "rspec --format progress")):
        if os.path.exists(os.path.join(cwd, marker)):
            hints.append(hint)
    if any(os.path.exists(os.path.join(cwd, m))
           for m in ("build.gradle", "build.gradle.kts")):
        hints.append("gradle test --console=plain")
    return hints


def lean_note(cwd, session_id):
    """Lean-output rules, injected on the first qualifying prompt of a session."""
    if not session_id:
        return ""
    seen = load_json(SESSIONS, {})
    if session_id in seen:
        return ""
    seen[session_id] = True
    if len(seen) > 50:  # sessions come and go; keep the file tiny
        seen = dict(list(seen.items())[-50:])
    try:
        save_json(SESSIONS, seen)
    except OSError:
        pass
    hints = test_hints(cwd)
    runners = f" ({'; '.join(hints)})" if hints else ""
    return ("[claude-token] Before starting any implementation: once you have "
            "actually looked at the code, state the real scope in one line "
            "(which files, roughly how much), then use AskUserQuestion to let "
            "the user choose — continue on this model, or delegate to a "
            "subagent on a cheaper one. Never estimate scope from the request "
            "alone, and never propose switching /model mid-session (the "
            "prompt cache is per model: the whole conversation reloads "
            "uncached).\n"
            "[claude-token] Lean-output rules for this session: run tests "
            f"with quiet reporters{runners}; never dump raw API/HTML bodies — "
            "filter with `jq`/`grep` or pipe through `claude-token toon` "
            "(HTML: add `--keep script,css` when the user's analysis targets "
            "JS or CSS — match what they asked); "
            "inspect DOM via selectors/snippets, never full-page dumps; long "
            "command output goes to a file, then read only its tail; read "
            "PDFs by narrow page ranges (each page ~1.5-3k tokens); each "
            "image/screenshot costs ~1.1-1.6k tokens — do not re-capture "
            "what you already saw.")


def cmd_posttool():
    """PostToolUse hook: when a tool result is huge, tell the model how to
    avoid the next one. Reactive but deterministic — it fires no matter what
    the model forgot, and works for any language or tool."""
    if level() <= 0:
        return
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        return
    ti = data.get("tool_input") or {}
    if not isinstance(ti, dict):
        ti = {}
    fp = (ti.get("file_path") or "").lower()
    # Images: the response carries megabytes of base64 that cost only ~1.1-1.6k
    # tokens (vision resizes to <=1568px) — byte size lies, so stay silent.
    if fp.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")):
        return
    resp = data.get("tool_response")
    if isinstance(resp, str):
        size = len(resp)
    elif resp:
        size = len(json.dumps(resp, ensure_ascii=False))
    else:
        return
    if size < BIG_RESULT:
        return

    cmd = (ti.get("command") or ti.get("url") or "").lower()
    tool = data.get("tool_name", "tool")
    if fp.endswith(".pdf"):
        hint = ("request only the pages you actually need "
                "(pages=\"2-5\") — each PDF page costs ~1.5-3k tokens")
    elif tool == "WebFetch" or "curl" in cmd or "wget" in cmd:
        hint = ("filter the body instead of dumping it raw: `| jq .field`, "
                "`| grep`, or `| claude-token toon` (JSON/HTML aware)")
    elif any(r in cmd for r in TEST_RUNNERS):
        hint = ("use the quiet reporter (-q / --reporter=dot / --silent) and "
                "show only the failures")
    else:
        hint = ("redirect to a file and read only the tail: "
                "`cmd > /tmp/out 2>&1; tail -30 /tmp/out`")
    ctx = (f"[claude-token] That {tool} result was ~{size // 4:,} tokens and "
           f"now rides in the context every turn until /compact. Next time, "
           f"{hint}.")
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PostToolUse", "additionalContext": ctx}}))
