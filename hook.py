"""UserPromptSubmit hook: injects lean-output rules, RAG line pointers and the
compact-memory pointer.

It never rewrites the prompt. The old version paid a full cold `claude -p`
session (~17.7k input tokens, measured over 135 runs) to translate and
"improve" each request — a lossy relay that could, and did, alter what the
user actually asked. The request now reaches the model exactly as written."""
import json
import os
import sys

from common import GUARD_ENV, level, window_for
from lean import lean_note
from memory import compact_pointer
from rag import hook_hits as rag_hits
from usage import tail_usage

# Prompts that are plain questions get routed: concise-answer directive and a
# /model haiku tip, instead of a full agentic response.
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


def is_simple_question(prompt, words):
    p = prompt.lower().strip()
    if words > 40 or any(v in p for v in BUILD_VERBS):
        return False
    return p.endswith("?") or any(p.startswith(q) for q in QUESTION_STARTS)


def context_pct(transcript, session_id):
    u = tail_usage(transcript or "") or {}
    ctx = (u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0)
           + u.get("cache_creation_input_tokens", 0))
    return ctx / window_for(session_id) * 100


def cmd_hook():
    # Recursion guard: the session digest shells out to `claude -p`, which
    # fires this hook in the child session — nothing to inject there.
    if os.environ.get(GUARD_ENV):
        return
    lv = level()
    if lv <= 0:
        return
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        return
    prompt = (data.get("prompt") or "").strip()
    words = len(prompt.split())
    # Skip what rules can't improve: commands, tiny replies, huge prompts.
    if prompt.startswith(("/", "!")) or words < 4 or words > 200:
        return

    # Notes are read by the model, not the user — English tokenizes ~20% cheaper.
    notes = []
    cwd = data.get("cwd") or os.getcwd()
    # Level 1+: lean-output rules, once per session, scoped to this project's
    # stack (only the test runners it actually uses — never a generic list).
    building = any(verb in prompt.lower() for verb in BUILD_VERBS)
    rules = lean_note(cwd, data.get("session_id"), building)
    if rules:
        notes.append(rules)

    # A /compact just emptied the context. The transcript kept the dropped
    # turns, so say so — in a pointer, not the content: what enters the
    # context from here is the user's call, not the hook's.
    ptr = compact_pointer(data.get("transcript_path"), data.get("session_id"))
    if ptr:
        notes.append(ptr)

    if lv >= 2:
        # Context pruning nudge: history is the silent token leak — every
        # response pays for it. Above 70% fill, stop rereads and surface
        # /compact.
        pct = context_pct(data.get("transcript_path"), data.get("session_id"))
        if pct >= 70:
            notes.append(
                f"[claude-token] Context {pct:.0f}% full: do not reread files "
                "already read, quote only snippets, and suggest /compact to "
                "the user (or /clear if the topic changed).")

        # Plain questions don't need an agentic answer — ask for a short reply.
        if is_simple_question(prompt, words):
            notes.append(
                "[claude-token] Simple question: answer briefly and directly, "
                "no tools unless needed. If the session is questions-only, "
                "mention that /model haiku is several times cheaper.")
            print("\n".join(notes))
            return

        # RAG: point Claude at the relevant line ranges up front so it reads
        # snippets instead of dumping whole files into context.
        hits = rag_hits(cwd, prompt)
        if hits:
            notes.append(
                "[claude-token] RAG — likely relevant spots, code before docs "
                "(Read only these ranges with offset/limit instead of whole "
                "files): "
                + " · ".join(hits))

    if notes:
        print("\n".join(notes))
