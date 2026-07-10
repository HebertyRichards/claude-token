"""Session memory: auto-digest each session when it ends (SessionEnd hook),
then `recall <query>` searches digests + transcripts locally — so past work
can be consulted at zero context cost instead of resuming heavy chats."""
import glob
import json
import os
import subprocess
import sys
import time

from common import CLAUDE_DIR, GUARD_ENV, HOME, PROJECTS
from rag import tokenize

DIGEST_DIR = os.path.join(CLAUDE_DIR, "claude-token-digests")
MIN_SIZE = 50_000     # don't digest tiny sessions (nothing worth remembering)
RECALL_DAYS = 60      # how far back transcript search goes


def session_texts(path, cap=300):
    """User prompts and Claude's text replies from a transcript, in order."""
    out = []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                if '"message"' not in line:
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if d.get("isSidechain"):
                    continue
                m = d.get("message") or {}
                c = m.get("content")
                if m.get("role") == "user" and isinstance(c, str):
                    if not c.startswith("<"):  # skip harness-injected blocks
                        out.append("USER: " + c[:cap])
                elif m.get("role") == "assistant" and isinstance(c, list):
                    for b in c:
                        if b.get("type") == "text" and b.get("text"):
                            out.append("CLAUDE: " + b["text"][:cap])
    except OSError:
        pass
    return out


def cmd_digest():
    """SessionEnd hook: summarize the finished session into a local digest."""
    if os.environ.get(GUARD_ENV):  # digests of our own `claude -p` calls: skip
        return
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        return
    path = data.get("transcript_path", "")
    sid = data.get("session_id", "unknown")
    if not path or not os.path.exists(path) or os.path.getsize(path) < MIN_SIZE:
        return

    parts = session_texts(path)
    if not parts:
        return
    text = "\n".join(parts)
    skeleton = text[:2000] + "\n...\n" + text[-6000:] if len(text) > 8000 else text

    meta = (
        "Summarize this coding session as a compact briefing in English: "
        "goal, key decisions, files touched, bugs fixed and how, final state, "
        "pending items. Max 250 words. Output only the briefing.\n\n" + skeleton
    )
    env = dict(os.environ, **{GUARD_ENV: "1"})
    try:
        out = subprocess.run(["claude", "-p", meta, "--model", "haiku"],
                             capture_output=True, text=True, timeout=60,
                             env=env, cwd=HOME)
        summary = out.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        summary = ""
    if not summary:  # offline fallback: keep the raw skeleton, still searchable
        summary = "(raw, no model available)\n" + skeleton[:3000]

    # Digests hold conversation content (raw text in the offline fallback):
    # dir 0700 + file 0600 so no other local user can read them.
    os.makedirs(DIGEST_DIR, exist_ok=True)
    os.chmod(DIGEST_DIR, 0o700)
    out_path = os.path.join(DIGEST_DIR, sid + ".md")
    fd = os.open(out_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with open(fd, "w", encoding="utf-8") as f:
        f.write(f"# session {sid}\n"
                f"- project: {data.get('cwd', '?')}\n"
                f"- date: {time.strftime('%Y-%m-%d %H:%M')}\n\n{summary}\n")


def score(query_toks, text):
    toks = tokenize(text)
    if not toks:
        return 0.0
    tf = {}
    for t in toks:
        tf[t] = tf.get(t, 0) + 1
    return sum(tf.get(q, 0) for q in query_toks) / (len(toks) ** 0.5)


def cmd_recall(argv):
    """Search past sessions (digests first, then transcripts) at zero token cost."""
    if not argv:
        print("uso: claude-token recall <o que você procura>")
        return
    query = " ".join(argv)
    q = set(tokenize(query))

    hits = []
    for p in glob.glob(os.path.join(DIGEST_DIR, "*.md")):
        try:
            with open(p, encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            continue
        s = score(q, text)
        if s > 0:
            hits.append((s, p, text))
    hits.sort(key=lambda x: -x[0])

    if hits:
        print(f"\nresumos de sessões (top {min(3, len(hits))}):\n")
        for s, p, text in hits[:3]:
            print("=" * 60)
            print(text.strip()[:1200])
            print()
    else:
        print("\nnenhum resumo bate — procurando nos transcripts brutos...\n")

    cutoff = time.time() - RECALL_DAYS * 86400
    raw = []
    for p in glob.glob(os.path.join(PROJECTS, "*", "*.jsonl")):
        if os.path.getmtime(p) < cutoff:
            continue
        best, best_s = "", 0.0
        for t in session_texts(p, cap=400):
            s = score(q, t)
            if s > best_s:
                best_s, best = s, t
        if best_s > 0:
            raw.append((best_s, p, best))
    raw.sort(key=lambda x: -x[0])
    if raw:
        print(f"trechos de conversas ({min(3, len(raw))}):\n")
        for s, p, snippet in raw[:3]:
            proj = os.path.basename(os.path.dirname(p))
            when = time.strftime("%Y-%m-%d", time.localtime(os.path.getmtime(p)))
            print(f"[{when} · {proj}]\n  {snippet[:300]}\n")
    elif not hits:
        print("nada encontrado.")
    print("dica: leve só o trecho útil para uma sessão NOVA — nunca dê resume "
          "num chat pesado só para perguntar algo.")
