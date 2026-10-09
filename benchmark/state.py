"""The results file: the plan, every attempt and the comparison, rewritten
atomically after each event. A killed process loses at most the run that was
in flight, and --resume needs nothing but this file."""
import glob
import os
import time

from common import load_json, now_iso, save_json

VERSION = 1
RESULTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
# A settled run is never executed again; any other status is still owed.
FINAL = ("ok", "check_failed", "error", "invalid")


def new(config, plan, environment):
    return {"version": VERSION, "id": time.strftime("bench-%Y%m%d-%H%M%S"),
            "created": now_iso(), "updated": now_iso(), "status": "running",
            "stop_reason": "", "config": config, "environment": environment,
            "guard": {}, "runs": plan, "summary": {}}


def path_for(state, out=None):
    return os.path.abspath(out) if out else os.path.join(RESULTS, state["id"] + ".json")


def save(path, state):
    state["updated"] = now_iso()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    save_json(path, state)


def load(path):
    state = load_json(path, None)
    if (not isinstance(state, dict) or state.get("version") != VERSION
            or not isinstance(state.get("runs"), list)):
        raise ValueError(f"{path}: não é um arquivo de resultados deste benchmark")
    return state


def latest(unfinished=False):
    """Newest results file, optionally only one that still has runs owed."""
    files = sorted(glob.glob(os.path.join(RESULTS, "*.json")),
                   key=os.path.getmtime, reverse=True)
    for path in files:
        state = load_json(path, None)
        if (isinstance(state, dict) and state.get("version") == VERSION
                and not (unfinished and state.get("status") == "done")):
            return path
    return ""


def reopen(state, retry_errors=False):
    """Make an unfinished file runnable again. Returns the attempts that were
    in flight when the process died: the caller measures what they spent."""
    orphans = []
    for run in state["runs"]:
        last = run["attempts"][-1] if run["attempts"] else None
        if run["status"] == "running":
            if last and last.get("status") == "running":
                last["status"] = "interrupted"
                last["error"] = "o processo morreu durante a execução"
                orphans.append(last)
            run["status"] = "pending"
        elif (run["status"] in ("limit", "interrupted")
              or (retry_errors and run["status"] == "error")):
            run["status"] = "pending"
    state["status"], state["stop_reason"] = "running", ""
    return orphans
