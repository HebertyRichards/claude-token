"""One headless run of the CLI, and the measurement of what it spent.

Usage is read from the transcript the run leaves behind, with the same
functions `claude-token report` uses: subagents included, deduped by message
id, priced by the local table. The CLI's own total is stored next to it as a
cross-check, never mixed in."""
import glob
import json
import os
import re
import shutil
import subprocess
import time

from common import (LEVEL_ENV, PROJECTS, cache_writes, price_row, same_version,
                    turn_cost)
from scenario import forbidden, is_secret
from usage import session_usage, subagent_files

MARKER = "[claude-token]"
WORKSPACE_PREFIX = "ctbench-"
FAMILIES = ("opus", "sonnet", "haiku", "fable", "mythos")
SUBAGENT_ENV = "CLAUDE_CODE_SUBAGENT_MODEL"   # the CLI's own switch
FILE_TOOLS = ("Read", "Edit", "Write", "MultiEdit", "NotebookEdit")
# What the CLI says when the account, not the task, is what failed.
LIMIT_RE = re.compile(
    r"usage limit|rate.?limit|limit reached|hit your limit|out of (extra )?usage"
    r"|credit balance|quota|\b429\b|overloaded", re.I)


def command(claude, model, session_id, tools, denied, run_budget=0, effort="", note=""):
    cmd = list(claude) + ["-p", "--output-format", "json", "--model", model,
                          "--session-id", session_id,
                          "--permission-mode", "acceptEdits",
                          # No MCP servers or connectors: an unattended run
                          # must not be able to open a pull request, merge or
                          # post anywhere through one.
                          "--strict-mcp-config"]
    if run_budget:
        cmd += ["--max-budget-usd", f"{run_budget:g}"]
    if effort:
        cmd += ["--effort", effort]
    if note:
        cmd += ["--append-system-prompt", note]
    # Variadic options, so they go last; the prompt travels on stdin.
    cmd += ["--disallowedTools", *denied]
    if tools:
        cmd += ["--allowedTools", *tools]
    return cmd


def guard_env(env, root):
    """Make git and gh inert for everything the run starts — the model's own
    commands, and whatever a script it is allowed to run decides to spawn
    (`npm version` commits and tags without a single git tool call).

    Deny rules stop a `git commit` the model types; this is what holds when
    the call comes from somewhere a rule does not look."""
    # A FILE where git expects a repository: no repository here, none that
    # `git init` can create, and none reachable with `git -C elsewhere` — so
    # no commit, branch or merge, in the working copy or in any other checkout.
    blocker = os.path.join(root, "git-disabled")
    with open(blocker, "w", encoding="utf-8") as f:
        f.write("git is disabled inside claude-token benchmark runs\n")
    env.update({
        "GIT_DIR": blocker,
        "GIT_TERMINAL_PROMPT": "0",
        # No transport at all: clone, fetch and push fail before any network.
        "GIT_CONFIG_COUNT": "2",
        "GIT_CONFIG_KEY_0": "protocol.allow", "GIT_CONFIG_VALUE_0": "never",
        "GIT_CONFIG_KEY_1": "credential.helper", "GIT_CONFIG_VALUE_1": "",
        # An environment token wins over gh's stored login: this one opens
        # nothing, and hides a real one if the user had it exported.
        "GH_TOKEN": "benchmark-sem-acesso", "GITHUB_TOKEN": "benchmark-sem-acesso",
        "GH_PROMPT_DISABLED": "1",
    })
    return env


def arm_env(arm, level, root, subagent_model=""):
    """The run's environment. The only thing that differs between the arms is
    the level the hooks see: at 0 they run and inject nothing — exactly what
    `claude-token level 0` does — so settings.json is never touched and no
    other session notices. Both arms get the same guards, and the same model
    for subagents when one was asked for (by default they inherit the main
    thread's)."""
    env = guard_env(dict(os.environ), root)
    env[LEVEL_ENV] = "0" if arm == "off" else str(level)
    if subagent_model:
        env[SUBAGENT_ENV] = subagent_model
    return env


def execute(cmd, prompt, cwd, env, timeout):
    """(exit code, stdout, stderr, seconds, timed out)."""
    started = time.time()
    proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    timed_out = False
    try:
        out, err = proc.communicate(prompt.encode("utf-8"), timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, err = proc.communicate()
        timed_out = True
    except BaseException:   # Ctrl+C: never leave the CLI running and spending
        proc.kill()
        proc.wait()
        raise
    return (proc.returncode, out.decode("utf-8", "replace"),
            err.decode("utf-8", "replace"), time.time() - started, timed_out)


def parse_result(stdout):
    """The CLI's `result` object, or None when it printed something else."""
    def pick(obj):
        if isinstance(obj, dict) and obj.get("type") == "result":
            return obj
        if isinstance(obj, list):
            for item in reversed(obj):
                if isinstance(item, dict) and item.get("type") == "result":
                    return item
        return None

    try:
        found = pick(json.loads(stdout))
        if found:
            return found
    except ValueError:
        pass
    for line in reversed(stdout.splitlines()):
        if line.lstrip().startswith("{"):
            try:
                found = pick(json.loads(line))
            except ValueError:
                continue
            if found:
                return found
    return None


def classify(exit_code, timed_out, result, stdout, stderr, plan_capped=False):
    """("", "") when the CLI finished the turn; else (status, reason) with
    status "limit" (the account ran out, or the plan's ceiling left no room to
    finish: stop the benchmark and resume later) or "error"."""
    failed = (timed_out or exit_code != 0 or result is None
              or bool(result.get("is_error")))
    if not failed:
        return "", ""
    said = str((result or {}).get("result") or "")
    if "budget" in str((result or {}).get("subtype") or "").lower():
        if plan_capped:
            return "limit", ("a execução foi cortada para não passar do teto do "
                             "plano; ela será refeita na retomada")
        return "error", "a execução passou do --run-budget e foi cortada"
    blob = " ".join((said, stderr[-2000:], "" if result else stdout[-2000:]))
    if LIMIT_RE.search(blob):
        return "limit", " ".join(blob.split())[:300]
    if timed_out:
        return "error", "tempo esgotado"
    reason = (result or {}).get("subtype") if result and not said else said
    return "error", " ".join(str(reason or blob).split())[:300] or f"exit {exit_code}"


def find_transcript(session_id, wait=3.0):
    """The run's transcript, wherever the CLI filed it."""
    deadline = time.time() + wait
    while True:
        hits = glob.glob(os.path.join(PROJECTS, "*", session_id + ".jsonl"))
        if hits or time.time() >= deadline:
            return hits[0] if hits else ""
        time.sleep(0.25)


def count_markers(path):
    """Hook outputs carrying claude-token's tag. Only hook attachments count:
    a tool result that merely quotes the tag is not an injection."""
    n = 0
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                if MARKER not in line or '"attachment"' not in line:
                    continue
                try:
                    att = json.loads(line).get("attachment") or {}
                except ValueError:
                    continue
                if str(att.get("type", "")).startswith("hook_") and MARKER in json.dumps(att):
                    n += 1
    except OSError:
        pass
    return n


def measure(path):
    """Everything a run spent, main thread and subagents, from its transcript.
    Each response is priced by the model that produced it: a subagent on
    another model than the main thread is billed at its own rate."""
    main, subs = session_usage(path)
    tot = dict(source="transcript", turns=0, sub_turns=0, inp=0, out=0, cr=0,
               cw_5m=0, cw_1h=0, cost=0.0, sub_cost=0.0, unpriced=0)
    models, sub_models = [], []
    for is_sub, entries in ((False, main), (True, subs)):
        seen = sub_models if is_sub else models
        for _ts, model, u in entries:
            if model == "<synthetic>":
                continue
            w5, w1 = cache_writes(u)
            tot["sub_turns" if is_sub else "turns"] += 1
            tot["inp"] += u.get("input_tokens", 0)
            tot["out"] += u.get("output_tokens", 0)
            tot["cr"] += u.get("cache_read_input_tokens", 0)
            tot["cw_5m"] += w5
            tot["cw_1h"] += w1
            cost = turn_cost(model, u)
            if cost is None:
                tot["unpriced"] += 1
            else:
                tot["cost"] += cost
                tot["sub_cost"] += cost if is_sub else 0.0
            if model not in seen:
                seen.append(model)
    tot["tokens"] = tot["inp"] + tot["out"] + tot["cr"] + tot["cw_5m"] + tot["cw_1h"]
    tot["cost"], tot["sub_cost"] = round(tot["cost"], 6), round(tot["sub_cost"], 6)
    tot["models"], tot["sub_models"] = models, sub_models
    tot["price_rows"] = {m: price_row(m) for m in models + sub_models}
    tot["markers"] = count_markers(path)
    tot["transcript"] = path
    return tot


def ran_as_requested(requested, models):
    """Did the main thread run the model that was asked for? An alias (opus,
    sonnet, haiku…) accepts whatever version it resolves to; a full id must
    be that exact version — "claude-opus-5" is not answered by Opus 5.5.
    Other aliases (default, opusplan…) cannot be checked and pass."""
    want = requested.lower().split("[")[0]
    if not models:
        return True
    if want in FAMILIES:
        return all(want in m.lower() for m in models)
    if not want.startswith("claude-"):
        return True
    return all(same_version(m, want) for m in models)


def _tool_calls(path):
    """[name, input, failed] for every tool call in one transcript file;
    `failed` is None when the call never got a result."""
    calls, order = {}, []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                if '"tool_use"' not in line and '"tool_result"' not in line:
                    continue
                try:
                    content = (json.loads(line).get("message") or {}).get("content")
                except ValueError:
                    continue
                for block in content if isinstance(content, list) else []:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "tool_use" and block.get("id") not in calls:
                        calls[block.get("id")] = [block.get("name"),
                                                  block.get("input") or {}, None]
                        order.append(block.get("id"))
                    elif (block.get("type") == "tool_result"
                          and block.get("tool_use_id") in calls):
                        calls[block["tool_use_id"]][2] = bool(block.get("is_error"))
    except OSError:
        pass
    return [calls[i] for i in order]


def replay_edits(path, old_root, new_root):
    """Rebuild what a run changed from its own Edit and Write calls, for a run
    recorded before its changes were saved. Returns the files touched. Changes
    made through the shell are not in those calls and are not rebuilt."""
    def inside(file_path):
        full = os.path.normcase(os.path.abspath(file_path))
        base = os.path.normcase(os.path.abspath(old_root))
        return os.path.relpath(full, base) if full.startswith(base + os.sep) else None

    touched = []
    for name, inp, failed in _tool_calls(path):
        if failed is not False or name not in ("Edit", "Write", "MultiEdit"):
            continue
        rel = inside(str(inp.get("file_path") or ""))
        if rel is None:
            continue
        target = os.path.join(new_root, rel)
        if name == "Write":
            text = str(inp.get("content") or "")
        else:
            with open(target, encoding="utf-8") as f:
                text = f.read()
            for edit in (inp.get("edits") if name == "MultiEdit" else [inp]) or []:
                old, new = str(edit.get("old_string") or ""), str(edit.get("new_string") or "")
                if old not in text:
                    raise ValueError(f"{rel}: um Edit do transcript não casa mais "
                                     "com o arquivo — o projeto mudou desde a execução")
                text = text.replace(old, new) if edit.get("replace_all") else text.replace(old, new, 1)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        if rel not in touched:
            touched.append(rel.replace(os.sep, "/"))
    return touched


def audit(path):
    """What the run actually did with its tools, main thread and subagents:
    how many shell commands ran, and any that went THROUGH although the fences
    forbid it. A refused call is the fence working; one that ran is the
    benchmark's own failure, and that run is thrown out."""
    out = {"commands": 0, "sub_commands": 0, "violations": []}
    for file, is_sub in [(path, False)] + [(p, True) for p in subagent_files(path)]:
        for name, inp, failed in _tool_calls(file):
            if not isinstance(inp, dict):
                continue
            if name in ("Bash", "PowerShell"):
                what = str(inp.get("command") or "")
                why = forbidden(what)
                if failed is not None:
                    out["sub_commands" if is_sub else "commands"] += 1
            elif name in FILE_TOOLS:
                target = str(inp.get("file_path") or inp.get("notebook_path") or "")
                why = "credenciais" if is_secret(os.path.basename(target)) else ""
                what = f"{name} {target}"
            else:
                continue
            if why and failed is False:
                out["violations"].append(("subagente — " if is_sub else "")
                                         + f"{why}: {' '.join(what.split())[:140]}")
    return out


def usage_from_cli(result):
    """Fallback when no transcript was found: the CLI's own totals."""
    u = (result or {}).get("usage")
    if not isinstance(u, dict):
        return None
    tot = dict(source="cli", turns=result.get("num_turns") or 0, sub_turns=0,
               inp=u.get("input_tokens", 0), out=u.get("output_tokens", 0),
               cr=u.get("cache_read_input_tokens", 0),
               cw_5m=u.get("cache_creation_input_tokens", 0), cw_1h=0,
               cost=round(float(result.get("total_cost_usd") or 0), 6),
               unpriced=0, models=sorted(result.get("modelUsage") or {}),
               markers=None, transcript="")
    tot["tokens"] = tot["inp"] + tot["out"] + tot["cr"] + tot["cw_5m"]
    return tot


def trim(result, answer):
    """What is worth keeping of the CLI's result object."""
    if not result:
        return None
    return {"subtype": result.get("subtype"), "is_error": bool(result.get("is_error")),
            "num_turns": result.get("num_turns"), "duration_ms": result.get("duration_ms"),
            "total_cost_usd": result.get("total_cost_usd"),
            "denials": len(result.get("permission_denials") or []),
            "answer": answer[:2000]}


def archive(path, dest_dir):
    """Move a run's transcript (and its subagents folder) next to the results,
    and drop the project folder the CLI created for the temp workspace.
    Returns the new path."""
    os.makedirs(dest_dir, exist_ok=True)
    project = os.path.dirname(path)
    new = os.path.join(dest_dir, os.path.basename(path))
    shutil.move(path, new)
    side = os.path.splitext(path)[0]
    if os.path.isdir(side):
        shutil.move(side, os.path.splitext(new)[0])
    # Only ever a folder this benchmark caused: its name is the slug of a
    # ctbench- workspace that no longer exists.
    if WORKSPACE_PREFIX in os.path.basename(project):
        shutil.rmtree(project, ignore_errors=True)
    return new
