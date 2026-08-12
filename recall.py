"""Past sessions, addressed by id — zero model calls.

Every conversation is already on disk in ~/.claude/projects/*/*.jsonl. There
used to be a Haiku pass here that summarized each finished session so the
summaries could be searched; it was deleted. Summarizing spends tokens to
produce a lossy copy of a file that was already complete and free to read.
`sessions` lists what exists, `recall` finds which ids mention something, and
`memory <id>` prints what was actually done in one."""
import glob
import json
import os
import sys
import time

from common import PROJECTS
from rag import tokenize

RECALL_DAYS = 60


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
                if d.get("isSidechain") or d.get("isCompactSummary"):
                    continue
                m = d.get("message") or {}
                c = m.get("content")
                if m.get("role") == "user" and isinstance(c, str):
                    if not c.startswith(("<", "/", "!")):
                        out.append("USER: " + c[:cap])
                elif m.get("role") == "assistant" and isinstance(c, list):
                    for b in c:
                        if b.get("type") == "text" and b.get("text"):
                            out.append("CLAUDE: " + b["text"][:cap])
    except OSError:
        pass
    return out


def iter_sessions(days):
    """(mtime, path, id, project, first user prompt) for recent transcripts."""
    cutoff = time.time() - days * 86400
    for p in glob.glob(os.path.join(PROJECTS, "*", "*.jsonl")):
        try:
            mt = os.path.getmtime(p)
        except OSError:
            continue
        if mt < cutoff:
            continue
        first = next((t[6:] for t in session_texts(p, cap=90)
                      if t.startswith("USER: ")), "")
        yield mt, p, os.path.splitext(os.path.basename(p))[0], \
            os.path.basename(os.path.dirname(p)), first


def arg_days(argv, default):
    if "--days" in argv:
        i = argv.index("--days")
        try:
            return int(argv[i + 1])
        except (IndexError, ValueError):
            print(f"--days sem número — usando {default}", file=sys.stderr)
    return default


def cmd_sessions(argv):
    """List past sessions by id. Reading a transcript costs nothing."""
    days = arg_days(argv, 14)
    rows = sorted(iter_sessions(days), reverse=True)
    if not rows:
        print(f"nenhuma sessão nos últimos {days} dias.")
        return
    print(f"{len(rows)} sessões nos últimos {days} dias "
          f"(id curto basta em `claude-token memory <id>`):\n")
    for mt, _, sid, proj, first in rows[:60]:
        when = time.strftime("%d/%m %H:%M", time.localtime(mt))
        print(f"{sid[:8]}  {when}  {proj[-28:]:<28}  {first[:60]}")


def score(query_toks, text):
    toks = tokenize(text)
    if not toks:
        return 0.0
    tf = {}
    for t in toks:
        tf[t] = tf.get(t, 0) + 1
    return sum(tf.get(q, 0) for q in query_toks) / (len(toks) ** 0.5)


def cmd_recall(argv):
    """Find which past sessions mention something, and return their ids."""
    days = arg_days(argv, RECALL_DAYS)
    # Drop "--days N" from the query words, keeping any other digits: a search
    # for "erro 500" must stay a search for "erro 500".
    terms, skip = [], False
    for a in argv:
        if skip:
            skip = False
            continue
        if a == "--days":
            skip = True
        elif not a.startswith("--"):
            terms.append(a)
    if not terms:
        print("uso: claude-token recall <o que você procura> [--days N]")
        print("depois: claude-token memory <id>  (traz o que foi feito na sessão)")
        return
    q = set(tokenize(" ".join(terms)))

    hits = []
    for mt, p, sid, proj, _ in iter_sessions(days):
        best, best_s = "", 0.0
        for t in session_texts(p, cap=400):
            s = score(q, t)
            if s > best_s:
                best_s, best = s, t
        if best_s > 0:
            hits.append((best_s, mt, sid, proj, best))
    if not hits:
        print("nada encontrado.")
        return
    hits.sort(reverse=True)
    print(f"{min(6, len(hits))} sessões (id curto -> `claude-token memory <id>`):\n")
    for _, mt, sid, proj, snippet in hits[:6]:
        when = time.strftime("%d/%m %H:%M", time.localtime(mt))
        print(f"[{sid[:8]}] {when} · {proj[-32:]}")
        print(f"  {' '.join(snippet.split())[:220]}\n")
    print("Traga só a sessão certa para um chat NOVO — nunca dê resume num "
          "chat pesado só para consultar algo.")
