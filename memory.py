"""Compact memory: /compact empties the model's context, not the transcript.
The discarded turns stay on disk in the same .jsonl (the boundary is just a
marker line), so what was done can be rebuilt from the file — mechanically,
with no model call and nothing rewritten. Capture is free, so it always runs;
injection costs tokens, so the hook only drops a short pointer and the full
note is loaded on request."""
import glob
import json
import os
import re
import subprocess
import sys
import time

from common import CLAUDE_DIR, PROJECTS, load_json, save_json
from lean import EDIT_TOOLS, TEST_RUNNERS

MEM_DIR = os.path.join(CLAUDE_DIR, "claude-token-memory")
SEEN = os.path.join(MEM_DIR, "_seen.json")
MAX_PROMPT = 2000     # chars kept per user request — a guard against pasted
                      # logs, not a summary: decisions listed in a prompt fit
MAX_REPLY = 400       # chars kept of the assistant's final reply per request
MAX_ITEMS = 40        # cap per section, so the note can't grow unbounded
# `git commit -m` only counts at a statement boundary. Without the anchor the
# string matches inside any script that merely *mentions* committing (a test
# fixture, a heredoc) and the note reports commits that never happened.
COMMIT_MSG = re.compile(
    r"""(?:^|[;&|]\s*|\bthen\s+)git\s+commit\b[^\n]*?-m\s*["']([^"']{1,120})""")
# The closing tag repeats the id attribute: </pasted_content id="4eb5">.
PASTED = re.compile(
    r"\s*<pasted_content\b[^>]*>.*?</pasted_content\b[^>]*>\s*", re.S)


def scan(path):
    """Everything worth remembering from a transcript, extracted mechanically.

    No model, no summary: file paths, commit messages, commands and the user's
    own words, kept verbatim. A summarizer would be a second chance to alter
    the request — the whole point is that it can't. The why comes from two
    places that are also verbatim: what the user answered when asked to choose
    (AskUserQuestion), and the head of the assistant's last reply to each
    request — quoted and cut, never rephrased."""
    info = {"boundaries": 0, "dropped": 0, "files": {}, "commits": [],
            "prompts": [], "replies": [], "decisions": [], "tests": [],
            "cwd": ""}
    cur = None  # index of the request the assistant is currently answering
    try:
        f = open(path, encoding="utf-8", errors="replace")
    except OSError:
        return info
    with f:
        for line in f:
            if '"compact_boundary"' not in line and '"message"' not in line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if d.get("isSidechain"):
                continue
            info["cwd"] = d.get("cwd") or info["cwd"]

            if d.get("subtype") == "compact_boundary":
                info["boundaries"] += 1
                meta = d.get("compactMetadata") or {}
                dropped = meta.get("cumulativeDroppedTokens")
                if isinstance(dropped, int):
                    info["dropped"] = max(info["dropped"], dropped)
                else:
                    info["dropped"] += max(0, (meta.get("preTokens") or 0)
                                           - (meta.get("postTokens") or 0))
                continue

            # An answered AskUserQuestion is a decision in the user's own
            # words; the harness stores it structured, question -> answer.
            answers = (d.get("toolUseResult") or {}).get("answers") \
                if isinstance(d.get("toolUseResult"), dict) else None
            if isinstance(answers, dict):
                for q, a in answers.items():
                    pair = (" ".join(str(q).split()), " ".join(str(a).split()))
                    if pair not in info["decisions"]:
                        info["decisions"].append(pair)

            m = d.get("message") or {}
            content = m.get("content")
            if m.get("role") == "user" and isinstance(content, str):
                # Skip harness blocks, slash commands, and the summary that
                # /compact itself injects (it is the thing being replaced).
                # A paste is material, not the ask: left in, it eats the
                # whole budget and the request typed after it is cut off.
                content = PASTED.sub(
                    lambda p: f"[pasted {len(p.group(0)):,} chars] ", content)
                if d.get("isCompactSummary") or content.startswith(("<", "/", "!")):
                    continue
                text = " ".join(content.split())[:MAX_PROMPT]
                if len(text.split()) < 3:
                    continue  # "sim", "ok": the reply still belongs to `cur`
                # A prompt can appear twice (a retry, or the segment the
                # compact preserved) — record the ask, not the replays.
                if text in info["prompts"]:
                    cur = info["prompts"].index(text)
                else:
                    info["prompts"].append(text)
                    info["replies"].append("")
                    cur = len(info["prompts"]) - 1
            elif m.get("role") == "assistant" and isinstance(content, list):
                for b in content:
                    if b.get("type") == "text" and cur is not None:
                        # Overwritten as the turn goes on, so what survives
                        # is the last thing said — the conclusion, not the
                        # "let me check" before a tool call.
                        reply = " ".join((b.get("text") or "").split())
                        if reply:
                            info["replies"][cur] = reply[:MAX_REPLY]
                        continue
                    if b.get("type") != "tool_use":
                        continue
                    inp = b.get("input") or {}
                    if not isinstance(inp, dict):
                        continue
                    name = b.get("name")
                    if name in EDIT_TOOLS:
                        fp = inp.get("file_path") or inp.get("notebook_path")
                        if fp:
                            info["files"][fp] = info["files"].get(fp, 0) + 1
                    elif name == "Bash":
                        cmd = " ".join((inp.get("command") or "").split())
                        if not cmd:
                            continue
                        msg = COMMIT_MSG.search(cmd)
                        if msg:
                            info["commits"].append(msg.group(1))
                        elif any(r in cmd for r in TEST_RUNNERS):
                            if cmd not in info["tests"]:
                                info["tests"].append(cmd[:160])
    info["git"] = git_state(info["cwd"])
    return info


def git_state(cwd):
    """Uncommitted work, read from git — still zero model tokens (this shells
    out to git, never to a model).

    Compacting *without* committing is the normal case, not the exception, and
    it is the one git itself cannot reconstruct later: the transcript says
    which files were touched, the working tree says how much changed in them.
    Neither is recoverable from the other."""
    if not cwd or not os.path.isdir(cwd):
        return {}

    def git(*args):
        try:
            r = subprocess.run(("git", "-C", cwd) + args, capture_output=True,
                               text=True, timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            return ""
        return r.stdout.strip() if r.returncode == 0 else ""

    if not git("rev-parse", "--is-inside-work-tree"):
        return {}
    return {"branch": git("rev-parse", "--abbrev-ref", "HEAD"),
            "head": git("log", "-1", "--format=%h %s"),
            "dirty": git("status", "--porcelain")[:4000],
            "stat": git("diff", "--stat", "HEAD")[:2000]}


def render(info, session_id):
    """The full note. Labels in English: its reader is the model, which pays
    per token; everything quoted stays in the language it was written in."""
    out = [f"# session memory {session_id}"]
    if info["cwd"]:
        out.append(f"- project: {info['cwd']}")
    if info["boundaries"]:
        out.append(f"- compactions: {info['boundaries']} "
                   f"({info['dropped']:,} tokens left the context and are here)")
    out.append(f"- generated: {time.strftime('%Y-%m-%d %H:%M')} — mechanical "
               "extraction from the transcript, no model call")

    def section(title, items, fmt=str, newest=True):
        """Over the cap, chronological sections keep the END: the latest
        requests and decisions are the state the work was left in."""
        if not items:
            return
        out.append(f"\n## {title}")
        extra = len(items) - MAX_ITEMS
        if extra > 0 and newest:
            out.append(f"- ... {extra} earlier ones omitted")
            items = items[-MAX_ITEMS:]
        for it in items[:MAX_ITEMS]:
            out.append("- " + fmt(it))
        if extra > 0 and not newest:
            out.append(f"- ... and {extra} more")

    section("user decisions (asked -> answered, verbatim)", info["decisions"],
            lambda qa: f"{qa[0]} -> {qa[1]}")
    section(f"user requests (verbatim) => final reply (first {MAX_REPLY} "
            "chars, verbatim)", list(zip(info["prompts"], info["replies"])),
            lambda pr: pr[0] + (f"\n  => {pr[1]}" if pr[1] else ""))
    files = sorted(info["files"].items(), key=lambda kv: -kv[1])
    # Ranked by edit count, not by time: here the top is what matters.
    section("edited files", files, lambda kv: f"{kv[0]}  ({kv[1]}x)",
            newest=False)
    section("commits", info["commits"])
    section("tests and builds run", info["tests"])

    g = info.get("git") or {}
    if g.get("dirty"):
        out.append("\n## UNCOMMITTED work (state that exists only here)")
        out.append(f"- branch {g.get('branch') or '?'} · last commit: "
                   f"{g.get('head') or '(none)'}")
        out.append("```")
        out.append(g["dirty"])
        if g.get("stat"):
            out.append("")
            out.append(g["stat"])
        out.append("```")
    elif g:
        out.append(f"\n## git\n- clean tree on {g.get('branch') or '?'} · "
                   f"last commit: {g.get('head') or '(none)'}")
    return "\n".join(out) + "\n"


def write_note(info, session_id):
    """Persist the note. Holds conversation content: 0700 dir, 0600 file."""
    text = render(info, session_id)
    os.makedirs(MEM_DIR, exist_ok=True)
    os.chmod(MEM_DIR, 0o700)
    path = os.path.join(MEM_DIR, f"{session_id}.md")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with open(fd, "w", encoding="utf-8") as f:
        f.write(text)
    return path, text


def compact_pointer(transcript, session_id):
    """Short note for the first prompt after a new /compact, else ''.

    Deliberately a pointer, not the content: ~50 tokens ride in the context,
    and whether to pay for the rest is the user's decision, not the hook's."""
    if not transcript or not session_id or not os.path.exists(transcript):
        return ""
    info = scan(transcript)
    if not info["boundaries"]:
        return ""
    seen = load_json(SEEN, {})
    if not isinstance(seen, dict):
        seen = {}
    if seen.get(session_id, 0) >= info["boundaries"]:
        return ""

    _, text = write_note(info, session_id)
    seen[session_id] = info["boundaries"]
    if len(seen) > 50:  # sessions come and go; keep the file tiny
        seen = dict(list(seen.items())[-50:])
    try:
        save_json(SEEN, seen)
    except OSError:
        pass

    # Uncommitted work is the fragile part: nothing outside this note and the
    # working tree records it, so it leads the pointer.
    dirty = len([l for l in (info.get("git") or {}).get("dirty", "").splitlines()
                 if l.strip()])
    warn = (f"{dirty} files are modified and NOT committed. " if dirty else "")
    # English: this line is read by the model, not the user.
    return (
        f"[claude-token] A /compact dropped ~{info['dropped']:,} tokens from "
        f"your context. They are NOT lost — the transcript kept them, and "
        f"{len(info['files'])} edited files, {len(info['prompts'])} user "
        f"requests with the reply each got, and {len(info['decisions'])} "
        f"decisions the user made were recovered from disk verbatim (no "
        f"model, no rewriting). "
        f"{warn}Loading the full note costs ~{len(text) // 4:,} tokens. Before "
        f"assuming anything about earlier work, ASK the user "
        f"(AskUserQuestion) whether to load it with `claude-token memory` or "
        f"continue without it — do not decide alone.")


def latest_transcript(cwd):
    """The most recently touched transcript for this project."""
    # Claude Code names the folder after the path with every non-alphanumeric
    # turned into "-": "/" and "." on POSIX, also ":" and "\" on Windows.
    guess = os.path.join(PROJECTS, re.sub(r"[^A-Za-z0-9]", "-", cwd))
    files = glob.glob(os.path.join(guess, "*.jsonl"))
    if not files:
        files = glob.glob(os.path.join(PROJECTS, "*", "*.jsonl"))
    return max(files, key=os.path.getmtime) if files else ""


def resolve(arg):
    """A path, a full session id, or an unambiguous id prefix."""
    if os.path.exists(arg):
        return arg
    hits = [p for p in glob.glob(os.path.join(PROJECTS, "*", "*.jsonl"))
            if os.path.basename(p).startswith(arg)]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        print(f"sessão '{arg}' não encontrada — veja `claude-token sessions`.",
              file=sys.stderr)
        return ""
    print(f"'{arg}' casa com {len(hits)} sessões — use mais caracteres do id:",
          file=sys.stderr)
    for p in sorted(hits, key=os.path.getmtime, reverse=True)[:8]:
        print("  " + os.path.splitext(os.path.basename(p))[0], file=sys.stderr)
    return ""


def cmd_memory(argv):
    """Print what was recovered from a session — costs zero model tokens."""
    if argv:
        path = resolve(argv[0])
        if not path:
            return
    else:
        path = latest_transcript(os.getcwd())
    if not path:
        print("nenhum transcript encontrado para este projeto.")
        return
    sid = os.path.splitext(os.path.basename(path))[0]
    info = scan(path)
    if not any((info["prompts"], info["files"], info["commits"])):
        print(f"nada registrado ainda em {os.path.basename(path)}.")
        return
    text = render(info, sid)
    print(text, end="")
    if not info["boundaries"]:
        print("\n(sessão ainda não compactada — isto é um espelho do que já "
              "está no contexto)", file=sys.stderr)
