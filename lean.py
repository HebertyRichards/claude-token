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
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
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


def lean_note(cwd, session_id, building=True):
    """Session rules, each given once: the core on the first qualifying
    prompt, the scope rule the first time something is going to be built."""
    if not session_id:
        return ""
    seen = load_json(SESSIONS, {})
    # 1: core rules given; 2: scope rule given too (True is the old marker).
    have = 2 if seen.get(session_id) is True else int(seen.get(session_id) or 0)
    want = 2 if building else 1
    if have >= want:
        return ""
    seen[session_id] = want
    if len(seen) > 50:  # sessions come and go; keep the file tiny
        seen = dict(list(seen.items())[-50:])
    try:
        save_json(SESSIONS, seen)
    except OSError:
        pass
    parts = []
    if have < 1:
        hints = test_hints(cwd)
        runners = f" ({'; '.join(hints)})" if hints else ""
        # Every character here is written to the prompt cache at the 1-hour
        # rate (twice the input price) and read back on every later turn: the
        # rules only pay for themselves while they stay short.
        # The rule on comments says which side wins, not "go and verify":
        # told to confirm comments in the implementation, the model opened
        # file after file to check one, and a one-file question cost up to
        # 2.4 times as much.
        parts.append(
            "[claude-token] Session rules. The code decides: comments and "
            "docs may be stale. Where they disagree, follow the code; do not "
            "open other files just to check a comment. Keep tool output "
            "small: quiet test "
            f"reporters{runners}, grep/jq or `claude-token toon` instead of "
            "raw bodies, long output to a file then its tail, big files and "
            "PDFs by range, no screenshot captured twice.")
    if building:
        # Only when something is going to be built: on a plain question the
        # scope line and the model question are nothing but extra output.
        parts.append(
            "[claude-token] Before implementing: look at the code, state the "
            "real scope in one line, then ask the user (AskUserQuestion) "
            "whether to continue on this model or delegate to a cheaper "
            "subagent. Never suggest switching /model mid-session: the "
            "prompt cache is per model.")
    return "\n".join(parts)


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
    # An edit hands the harness the whole file (before, after, patch), but the
    # model only gets a one-line confirmation: the payload size says nothing
    # about what entered the context, and flagging it is a false alarm.
    if data.get("tool_name") in EDIT_TOOLS:
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
    # The advice has to fit the tool: a file read or a search has no command
    # whose output could be redirected.
    elif tool == "Read":
        hint = ("read only the lines you need (offset/limit), finding them "
                "with Grep first")
    elif tool in ("Grep", "Glob"):
        hint = "narrow the pattern or the path"
        if tool == "Grep":
            hint += (", or list the matching files first "
                     "(output_mode=files_with_matches) and cap it with head_limit")
    elif ti.get("command"):
        hint = ("redirect to a file and read only the tail: "
                "`cmd > /tmp/out 2>&1; tail -30 /tmp/out`")
    else:
        hint = "ask for less: a narrower query, a filter or a range"
    ctx = (f"[claude-token] That {tool} result was ~{size // 4:,} tokens and "
           f"now rides in the context every turn until /compact. Next time, "
           f"{hint}.")
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PostToolUse", "additionalContext": ctx}}))
