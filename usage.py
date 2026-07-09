"""Read Claude Code transcripts (~/.claude/projects/*/*.jsonl) and aggregate usage."""
import glob
import json
import os
from datetime import datetime, timedelta, timezone

from common import PROJECTS, parse_ts, periods, price_for


def iter_usage(path):
    """One (timestamp, model, usage) per assistant response, deduped by message id."""
    seen = {}
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
                if not u or d.get("isSidechain"):
                    continue
                seen[m.get("id") or d.get("uuid")] = (
                    d.get("timestamp", ""), m.get("model", ""), u)
    except OSError:
        pass
    return seen.values()


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
    """Totals for the last N days, split into ECO-on vs ECO-off buckets."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    ps = periods()
    zero = dict(turns=0, inp=0, out=0, cr=0, cw=0, cost=0.0)
    b = {"on": dict(zero), "off": dict(zero)}

    for path in glob.glob(os.path.join(PROJECTS, "*", "*.jsonl")):
        if os.path.getmtime(path) < cutoff.timestamp():
            continue
        for ts, model, u in iter_usage(path):
            dt = parse_ts(ts)
            if not dt or dt < cutoff:
                continue
            t = b["on"] if in_on_period(dt, ps) else b["off"]
            pi, po, pcr, pcw = price_for(model)
            inp, out = u.get("input_tokens", 0), u.get("output_tokens", 0)
            cr = u.get("cache_read_input_tokens", 0)
            cw = u.get("cache_creation_input_tokens", 0)
            t["turns"] += 1
            t["inp"] += inp; t["out"] += out; t["cr"] += cr; t["cw"] += cw
            t["cost"] += (inp * pi + out * po + cr * pcr + cw * pcw) / 1e6
    return b
