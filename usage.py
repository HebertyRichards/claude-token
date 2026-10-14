"""Read Claude Code transcripts (~/.claude/projects/*/*.jsonl, plus each
session's subagents/ folder) and aggregate usage."""
import glob
import json
import os
from datetime import datetime, timedelta, timezone

from common import PROJECTS, parse_ts, periods, turn_cost


def _read(path):
    """({id: entry} of the main thread, {id: entry} of sidechain lines), an
    entry being (timestamp, model, usage) per assistant response."""
    main, side = {}, {}
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                if '"usage"' not in line:
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                m = d.get("message") or {}
                u = m.get("usage")
                if not u:
                    continue
                (side if d.get("isSidechain") else main)[
                    m.get("id") or d.get("uuid")] = (
                    d.get("timestamp", ""), m.get("model", ""), u)
    except OSError:
        pass
    return main, side


def iter_usage(path):
    """One (timestamp, model, usage) per assistant response of the main
    thread, deduped by message id."""
    return _read(path)[0].values()


def subagent_files(path):
    """Transcripts of every subagent a session spawned. Recursive: agents
    started by a workflow sit one or two folders further down
    (subagents/workflows/<run>/agent-*.jsonl), and they bill the same."""
    folder = os.path.join(os.path.splitext(path)[0], "subagents")
    return sorted(glob.glob(os.path.join(folder, "**", "*.jsonl"), recursive=True))


def subagent_usage(path):
    """One list of entries per subagent the session spawned.

    A subagent's turns are billed like any other but are not in the session's
    own thread: the harness writes each one to
    <session>/subagents/agent-<id>.jsonl beside the transcript, every line
    flagged isSidechain. Leaving them out hid real spend — and hid what
    delegating to a cheaper model costs per response."""
    agents = []
    for sub in subagent_files(path):
        main, side = _read(sub)
        entries = list({**main, **side}.values())
        if entries:
            agents.append(entries)
    return agents


def session_usage(path):
    """(main entries, subagent entries) of one transcript, read once. Older
    harnesses wrote sidechain lines into the main file; those count too."""
    main, side = _read(path)
    subs = list(side.values())
    for entries in subagent_usage(path):
        subs.extend(entries)
    return list(main.values()), subs


def segment_usage(path):
    """Usage since the last /compact boundary — what the live context holds.
    Returns (entries, carried): entries as in iter_usage but only after the
    boundary, and carried = context tokens the compact summary kept
    (compactMetadata.postTokens; 0 if the session was never compacted)."""
    seen, carried = {}, 0
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                if '"compact_boundary"' in line:
                    try:
                        meta = json.loads(line).get("compactMetadata") or {}
                    except json.JSONDecodeError:
                        meta = {}
                    seen, carried = {}, meta.get("postTokens", 0)
                    continue
                if '"usage"' not in line:
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                m = d.get("message") or {}
                u = m.get("usage")
                if not u or d.get("isSidechain"):
                    continue
                seen[m.get("id") or d.get("uuid")] = (
                    d.get("timestamp", ""), m.get("model", ""), u)
    except OSError:
        pass
    return list(seen.values()), carried


def tail_usage(path, size=131072):
    """Latest usage block, reading only the file tail (keeps hooks fast).
    If a /compact boundary is the most recent event, returns a synthetic
    usage holding only the tokens the summary carried over."""
    try:
        with open(path, "rb") as f:
            f.seek(max(0, os.path.getsize(path) - size))
            lines = f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        if '"compact_boundary"' in line:
            try:
                meta = json.loads(line).get("compactMetadata") or {}
            except json.JSONDecodeError:
                meta = {}
            return {"input_tokens": meta.get("postTokens", 0)}
        if '"usage"' not in line:
            continue
        try:
            u = json.loads(line).get("message", {}).get("usage")
        except json.JSONDecodeError:
            continue
        if u:
            return u
    return None


def in_on_period(dt, ps):
    for p in ps:
        start, end = parse_ts(p["start"]), parse_ts(p.get("end"))
        if start and dt >= start and (end is None or dt <= end):
            return True
    return False


def collect(days):
    """Totals for the last N days: the main thread split into ECO-on vs
    ECO-off buckets, plus a "sub" bucket for everything subagents spent, and
    "models": per model id, how many responses of each kind and their cost."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    ps = periods()
    zero = dict(turns=0, inp=0, out=0, cr=0, cw=0, cost=0.0)
    b = {"on": dict(zero), "off": dict(zero), "sub": dict(zero), "models": {}}

    for path in glob.glob(os.path.join(PROJECTS, "*", "*.jsonl")):
        if os.path.getmtime(path) < cutoff.timestamp():
            continue
        main, subs = session_usage(path)
        for is_sub, entries in ((False, main), (True, subs)):
            for ts, model, u in entries:
                dt = parse_ts(ts)
                if not dt or dt < cutoff:
                    continue
                t = b["sub"] if is_sub else \
                    b["on"] if in_on_period(dt, ps) else b["off"]
                t["turns"] += 1
                t["inp"] += u.get("input_tokens", 0)
                t["out"] += u.get("output_tokens", 0)
                t["cr"] += u.get("cache_read_input_tokens", 0)
                t["cw"] += u.get("cache_creation_input_tokens", 0)
                # Unpriced model: count the tokens, leave the cost out rather
                # than bill it at some other model's rate (price_for warns
                # separately).
                cost = turn_cost(model, u) or 0.0
                t["cost"] += cost
                if model != "<synthetic>":
                    m = b["models"].setdefault(
                        model, {"turns": 0, "sub_turns": 0, "cost": 0.0})
                    m["sub_turns" if is_sub else "turns"] += 1
                    m["cost"] += cost
    return b
