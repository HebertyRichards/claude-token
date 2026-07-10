"""UserPromptSubmit hook: rewrites vague prompts into precise English ones
via Haiku, following the project's CLAUDE.md/AGENTS.md conventions."""
import json
import os
import re
import subprocess
import sys

from common import CONTEXT_WINDOW, GUARD_ENV, HOME
from lean import lean_note
from rag import hook_hits as rag_hits
from usage import tail_usage

# Prompts that are plain questions get routed: no rewrite, concise-answer
# directive, and a /model haiku tip — instead of a full agentic response.
QUESTION_STARTS = (
    "o que", "oq ", "como", "qual", "quais", "por que", "porque", "pq ",
    "quando", "onde", "explique", "me explica", "é possível", "e possivel",
    "dá pra", "da pra", "what", "how", "why", "which", "explain", "is it",
    "can i", "does",
)
BUILD_VERBS = ("crie", "criar", "faça", "faca", "implemente", "refatore",
               "adicione", "escreva", "monte", "gere", "corrija", "ajuste",
               "mude", "analise", "analisa", "planeje", "planeja", "otimize",
               "verifique", "create", "implement", "build", "add", "write",
               "fix", "refactor", "change", "analyze", "plan", "optimize",
               "review")
# A prompt that names files, paths, code fences or calls is already precise:
# the Haiku rewrite would spend quota (a full cold `claude -p` session) for
# little gain — the main saving of the rewrite is de-vaguing vague prompts.
PRECISE = re.compile(
    r"```"
    r"|\b\w[\w-]*\.(?:py|js|ts|tsx|jsx|java|go|rs|rb|php|cs|cpp|hpp|c|h|css"
    r"|scss|html|sql|json|ya?ml|toml|md|sh|lock)\b"
    r"|(?:^|\s)[\w.~-]*/[\w./-]+"   # path segments like src/app or ./run
    r"|\w+\([^)\n]{0,40}\)")        # function calls like foo(x)
# Model routing nudge for small tasks: heavy models burn their own scoped
# weekly window on trivial edits. Delegate to a fresh sonnet subagent —
# NOT /model: switching mid-session reloads the whole conversation uncached
# (the prompt cache is per model). Never haiku: it is not good enough for
# code, even simple code.
SIMPLE_NUDGE = (
    "[claude-token] This task looks small and localized. If a quick look "
    "confirms it (one flag, one config, a one-file edit), delegate it to a "
    "subagent on a cheaper model (Task/Agent tool, model sonnet) with a "
    "self-contained instruction — that spares the Fable/Opus weekly window. "
    "Do NOT suggest switching /model mid-session (the new model reloads the "
    "whole conversation uncached). Never use haiku for code.")


def is_simple_question(prompt, words):
    p = prompt.lower().strip()
    if words > 40 or any(v in p for v in BUILD_VERBS):
        return False
    return p.endswith("?") or any(p.startswith(q) for q in QUESTION_STARTS)


def context_pct(transcript):
    u = tail_usage(transcript or "") or {}
    ctx = (u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0)
           + u.get("cache_creation_input_tokens", 0))
    return ctx / CONTEXT_WINDOW * 100


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

    # Notes are read by the model, not the user — English tokenizes ~20% cheaper.
    notes = []
    cwd = data.get("cwd") or os.getcwd()
    # Lean-output rules, once per session, scoped to this project's stack
    # (only the test runners it actually uses — never a generic list).
    rules = lean_note(cwd, data.get("session_id"))
    if rules:
        notes.append(rules)
    # Context pruning nudge: history is the silent token leak — every response
    # pays for it. Above 70% fill, stop rereads and surface /compact.
    pct = context_pct(data.get("transcript_path"))
    if pct >= 70:
        notes.append(
            f"[claude-token] Context {pct:.0f}% full: do not reread files "
            "already read, quote only snippets, and suggest /compact to the "
            "user (or /clear if the topic changed).")

    # Model routing (lite): plain questions don't need the Haiku rewrite nor
    # an agentic answer — skip the rewrite entirely and ask for a short reply.
    if is_simple_question(prompt, words):
        notes.append(
            "[claude-token] Simple question: answer briefly and directly, no "
            "tools unless needed. If the session is questions-only, mention "
            "that /model haiku is ~5x cheaper.")
        print("\n".join(notes))
        return


    # RAG: point Claude at the relevant line ranges up front so it reads
    # snippets instead of dumping whole files into context.
    hits = rag_hits(cwd, prompt)
    if hits:
        notes.append(
            "[claude-token] RAG — likely relevant spots (Read only these "
            "ranges with offset/limit instead of whole files): "
            + " · ".join(hits))

    # Already-precise prompt: skip the rewrite entirely (no Haiku call) and
    # stay quiet. No local routing nudge here: word count can't tell a small
    # task from a discussion — only the Haiku classifier (below) sees intent.
    if PRECISE.search(prompt):
        if notes:
            print("\n".join(notes))
        return

    # Feed project conventions (CLAUDE.md / AGENTS.md) to the rewriter so the
    # optimized prompt already matches the project's language and patterns —
    # code that comes out right the first time is the biggest token saving.
    conventions = ""
    for name in ("CLAUDE.md", "AGENTS.md"):
        try:
            with open(os.path.join(cwd, name), encoding="utf-8", errors="replace") as f:
                conventions += f"\n--- {name} ---\n" + f.read(1500)
        except OSError:
            pass

    meta = (
        "First line of your output: 'COMPLEXITY: low' if the request is a "
        "small localized change (a flag, a rename, a config tweak, a "
        "one-file edit), otherwise 'COMPLEXITY: high'. Then, on the next "
        "line, rewrite the user request below as ONE precise, minimal-scope prompt "
        "in English for a coding agent. Keep every explicit detail and the "
        "original intent; add nothing new; state that unrequested extras "
        "(tests, docs, alternatives, boilerplate) must be skipped. "
        "If the text is not an actionable task (a question, an opinion, a "
        "remark), just translate it to English verbatim. Output only the "
        "result — never commentary or questions about the request.\n"
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
        rewritten = ""  # fail silently: the original prompt still goes through
    # The rewriter classifies complexity on its first line (costs ~zero — the
    # call was happening anyway); 'low' triggers the sonnet routing nudge.
    first, _, rest = rewritten.partition("\n")
    if "COMPLEXITY:" in first.upper():
        rewritten = rest.strip()
        if "low" in first.lower():
            notes.append(SIMPLE_NUDGE)
    if rewritten:
        notes.append(
            f"[claude-token] Optimized reading of the request — follow it: {rewritten}")
    if notes:
        print("\n".join(notes))
