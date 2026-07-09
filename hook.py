"""UserPromptSubmit hook: rewrites vague prompts into precise English ones
via Haiku, following the project's CLAUDE.md/AGENTS.md conventions."""
import json
import os
import subprocess
import sys

from common import GUARD_ENV, HOME


def cmd_hook():
    # Recursion guard: the `claude -p` call below would trigger this hook again.
    if os.environ.get(GUARD_ENV):
        return
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        return
    prompt = (data.get("prompt") or "").strip()
    words = len(prompt.split())
    # Skip what a rewrite can't improve: commands, tiny replies, long detailed prompts.
    if prompt.startswith(("/", "!")) or words < 4 or words > 200:
        return

    # Feed project conventions (CLAUDE.md / AGENTS.md) to the rewriter so the
    # optimized prompt already matches the project's language and patterns —
    # code that comes out right the first time is the biggest token saving.
    conventions = ""
    cwd = data.get("cwd") or os.getcwd()
    for name in ("CLAUDE.md", "AGENTS.md"):
        try:
            with open(os.path.join(cwd, name), encoding="utf-8", errors="replace") as f:
                conventions += f"\n--- {name} ---\n" + f.read(1500)
        except OSError:
            pass

    meta = (
        "Rewrite the user request below as ONE precise, minimal-scope prompt "
        "in English for a coding agent. Keep every explicit detail and the "
        "original intent; add nothing new; state that unrequested extras "
        "(tests, docs, alternatives, boilerplate) must be skipped. "
        "Output only the rewritten prompt.\n"
    )
    if conventions:
        meta += ("\nMake the rewritten prompt follow these project "
                 "conventions:\n" + conventions[:3200] + "\n")
    meta += "\nUser request:\n" + prompt

    env = dict(os.environ, **{GUARD_ENV: "1"})
    try:
        out = subprocess.run(
            ["claude", "-p", meta, "--model", "haiku"],
            capture_output=True, text=True, timeout=25, env=env, cwd=HOME,
        )
        rewritten = out.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return  # fail silently: the original prompt still goes through
    if rewritten:
        print(f"[claude-token] Optimized reading of the request — follow it: {rewritten}")
