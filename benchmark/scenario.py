"""The scenario: the task the user described on the command line — what to
ask, in which project, and how to tell whether it was done. Nothing is canned:
a number measured on someone else's toy project says little about the work
you actually do.

A run is unattended, so this module also fences it in: what never enters the
working copy, what the model is told to leave alone, and which tool calls are
refused outright. None of it is a sandbox; runner.guard_env() adds the layer
that holds when a rule is sidestepped."""
import hashlib
import os
import re
import shutil
import subprocess
import tempfile

from rag import SECRET_FILE

# Appended to every prompt, in BOTH arms, so they stay identical. The first
# half exists because a headless run has nobody to answer, and claude-token's
# own scope rule tells the model to ask the user which model to continue on:
# the ON arm would stall on that question, and "spent less because it stopped"
# would read as a saving. The second half is the first guard: say outright
# what an unattended run must not touch.
HEADLESS_NOTE = ("Non-interactive run: nobody is here to answer. Do not ask "
                 "questions and do not ask for confirmation or for a model "
                 "choice; continue on this model until the task is done. Do "
                 "not use git or gh (no branch, commit, push or merge) and do "
                 "not read or change credential files (.env, keys, tokens).")
# hook.py injects nothing for prompts outside this word range, which would
# make the two arms identical without anything saying so.
HOOK_WORDS = (4, 200)
# Folders that never enter the working copy: version-control history (an
# unattended run has no business in a repository), dependencies (huge, and
# rebuilt by --setup when the task needs them), tool caches and build output
# (derived, often larger than the source) and credential stores.
SKIP_DIRS = {".git", ".hg", ".svn", "__pycache__", "node_modules", ".venv", "venv",
             ".next", ".nuxt", ".turbo", ".tox", ".mypy_cache", ".ruff_cache",
             ".pytest_cache",
             ".ssh", ".aws", ".gnupg", ".kube", "secrets", ".secrets"}
KEY_FILE = re.compile(
    r"^(\.netrc|\.npmrc|\.pypirc|\.git-credentials|id_(rsa|dsa|ecdsa|ed25519).*)$"
    r"|\.(pem|key|p12|pfx|jks|keystore)$", re.I)
# The secret-shaped names the RAG index refuses (secrets.json, credentials.yaml)
# are withheld only as data files: password_reset.py is code, and a copy
# without it would not run.
DATA_EXTS = {"", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf",
             ".txt", ".xml", ".properties", ".csv"}
# Read/Edit are refused on these wherever they are, not only inside the copy.
SECRET_PATHS = ("**/.env*", "~/**/.env*", "//**/.env*", "~/.ssh/**", "~/.aws/**",
                "~/.gnupg/**", "~/.kube/**", "~/.config/gh/**",
                "~/.docker/config.json", "~/.claude/.credentials.json",
                "~/.netrc", "~/.npmrc", "~/.pypirc", "~/.git-credentials")
MAX_COPY = 500 * 1024 * 1024    # bytes per working copy; one is made per run
CHECK_TIMEOUT = 900
# Shell commands a run must not contain: git or gh in command position (the
# start, or after a separator, a subshell opener or a wrapper like sudo), and
# any mention of a credential file. Used twice: to refuse the user's own
# --setup/--check before anything runs, and to audit afterwards every command
# the model and its subagents actually ran.
_COMMAND_START = r"(?:^|[;&|(`]|\$\(|\b(?:sudo|env|command|exec|xargs|time)\s)\s*"
FORBIDDEN = (
    (re.compile(_COMMAND_START + r"(?:\S*[\\/])?(?:git|gh)(?:\.exe)?(?=\s|$)", re.I),
     "git/gh"),
    (re.compile(r"(?:^|[\s\"'=/\\])(?:\.env[\w.-]*|id_(?:rsa|dsa|ecdsa|ed25519)\b"
                r"|\.ssh[\\/]|\.aws[\\/]|\.git-credentials|\.netrc|\.npmrc)", re.I),
     "credenciais"),
)


_HEREDOC = re.compile(r"<<-?\s*(['\"]?)(\w+)\1[^\n]*\n.*?^\s*\2\s*$", re.S | re.M)
# What the shell prints when a command could not even start.
NOT_FOUND = re.compile(
    r"no such file|can't open file|cannot find module|module_not_found"
    r"|command not found|not recognized as|não é reconhecido", re.I)


def forbidden(command):
    """Why a shell command breaks the fences ("git/gh", "credenciais"), or "".
    The body of a heredoc is a script's text, not the command: a comment in
    it that says "does not touch .env" is a mention, not an access."""
    top_level = _HEREDOC.sub("", command or "")
    return next((why for pattern, why in FORBIDDEN if pattern.search(top_level)), "")


class ScenarioError(ValueError):
    pass


def full_prompt(sc):
    """What the model is asked: the user's words and nothing else. The note
    above travels in the system prompt (see runner.command), where it is the
    same in both arms, sits in the cached prefix, and does not leak its own
    words into what claude-token's hook searches the project for."""
    return sc["prompt"]


def is_secret(name):
    if name.lower().startswith(".env") or KEY_FILE.search(name):
        return True
    return (bool(SECRET_FILE.search(name))
            and os.path.splitext(name)[1].lower() in DATA_EXTS)


def skip(dirpath, names):
    """What stays out of the working copy (shutil.copytree's `ignore`)."""
    out = set()
    for name in names:
        if os.path.isdir(os.path.join(dirpath, name)):
            if name in SKIP_DIRS:
                out.add(name)
        elif name.endswith(".pyc") or is_secret(name):
            out.add(name)
    return out


def tree_size(root):
    """(files, bytes, withheld) of a working copy of `root`: what it would
    hold, and the secret-shaped files it would not."""
    files = size = 0
    withheld = []
    for dirpath, dirnames, filenames in os.walk(root):
        out = skip(dirpath, dirnames + filenames)
        dirnames[:] = [d for d in dirnames if d not in out]
        for name in filenames:
            if name in out:
                if is_secret(name):
                    withheld.append(os.path.relpath(os.path.join(dirpath, name), root)
                                    .replace(os.sep, "/"))
                continue
            try:
                size += os.path.getsize(os.path.join(dirpath, name))
                files += 1
            except OSError:
                pass
    return files, size, withheld


def allowed_tools(checks, extra):
    """What the model may run without asking: the commands that verify the
    task (a model that cannot run the tests cannot be expected to make them
    pass) and the user's own --allow rules. File tools are deliberately not
    listed: reading works inside the copy by default and edits are accepted
    there by the permission mode, while a bare `Edit` rule would accept edits
    anywhere on the machine."""
    tools = []
    for cmd in checks:
        word = (cmd.split() or [""])[0]
        if re.fullmatch(r"[A-Za-z0-9_.-]+", word):
            tools += [f"Bash({word})", f"Bash({word} *)"]
            if os.name == "nt":
                tools += [f"PowerShell({word})", f"PowerShell({word} *)"]
    return list(dict.fromkeys(tools + list(extra)))


def denied_tools():
    """Tool calls refused outright. A deny rule beats every allow rule,
    including the ones in the user's own settings."""
    shells = ("Bash", "PowerShell") if os.name == "nt" else ("Bash",)
    rules = [f"{shell}({cmd}{rest})" for shell in shells
             for cmd in ("git", "gh") for rest in ("", " *")]
    return rules + [f"{tool}({path})" for tool in ("Read", "Edit")
                    for path in SECRET_PATHS]


def describe(args):
    """The scenario, from the command line. Raises ScenarioError when it
    cannot be run as described."""
    if bool(args.prompt) == bool(args.prompt_file):
        raise ScenarioError("descreva a tarefa com --prompt \"...\" ou "
                            "--prompt-file ARQUIVO (um dos dois)")
    prompt = args.prompt
    if args.prompt_file:
        try:
            with open(args.prompt_file, encoding="utf-8") as f:
                prompt = f.read()
        except OSError as e:
            raise ScenarioError(f"--prompt-file: {e}") from None
    prompt = prompt.strip()

    workspace = os.path.realpath(args.workspace or os.getcwd())
    if not os.path.isdir(workspace):
        raise ScenarioError(f"--workspace não é uma pasta: {workspace}")
    try:
        temp = os.path.realpath(tempfile.gettempdir())
        inside = os.path.commonpath([workspace, temp]) == workspace
    except ValueError:      # different drives
        inside = False
    if inside:
        raise ScenarioError("--workspace contém a pasta temporária do sistema, "
                            "onde as cópias de trabalho são feitas; aponte "
                            "para a pasta do projeto")
    for rule in args.allow:
        if re.search(r"\(\s*(git|gh)\b", rule):
            raise ScenarioError(f"--allow {rule!r}: o benchmark não libera git "
                                "nem gh — as execuções não mexem em repositórios")
    # The user's own commands obey the same fences as the model's.
    for flag, cmds in (("--setup", args.setup), ("--check", args.check)):
        for cmd in cmds:
            if forbidden(cmd):
                raise ScenarioError(f"{flag} {cmd!r} envolve {forbidden(cmd)}: "
                                    "nenhuma etapa do benchmark mexe em "
                                    "repositório ou em credenciais")

    kind = (args.kind or "tarefa").strip()
    digest = hashlib.sha1(prompt.encode("utf-8")).hexdigest()[:6]
    sc = {"id": args.name or f"{kind}-{digest}", "kind": kind, "prompt": prompt,
          "workspace": workspace, "setup": list(args.setup), "check": []}
    words = len(full_prompt(sc).split())
    if not HOOK_WORDS[0] <= words <= HOOK_WORDS[1]:
        raise ScenarioError(
            f"o pedido tem {words} palavras; fora de {HOOK_WORDS[0]}-"
            f"{HOOK_WORDS[1]} o hook do claude-token não injeta nada e os dois "
            "braços seriam iguais")

    if args.unchanged:
        missing = [p for p in args.unchanged
                   if not os.path.isfile(os.path.join(workspace, p))]
        if missing:
            raise ScenarioError("--unchanged: não existe em --workspace: "
                                + ", ".join(missing))
        sc["check"].append({"unchanged": list(args.unchanged)})
    for pattern in args.expect:
        try:
            re.compile(pattern)
        except re.error as e:
            raise ScenarioError(f"--expect {pattern!r}: {e}") from None
        sc["check"].append({"answer": pattern})
    sc["check"] += [{"run": cmd} for cmd in args.check]
    sc["allowed_tools"] = allowed_tools(args.check, args.allow)

    sc["files"], sc["bytes"], sc["withheld"] = tree_size(workspace)
    if sc["bytes"] > MAX_COPY:
        raise ScenarioError(
            f"--workspace tem {sc['bytes'] / 1e6:,.0f} MB a copiar por execução "
            f"(teto: {MAX_COPY // 2**20} MB); aponte para uma pasta menor")
    return sc


def _shell(cmd, cwd, env):
    # The user's own command, typed the way they would type it in a terminal —
    # run in the same fenced environment as the model (git inert).
    return subprocess.run(cmd, cwd=cwd, env=env, shell=True, capture_output=True,
                          timeout=CHECK_TIMEOUT)


def _sha(path):
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return ""


def _tail(done):
    text = (done.stdout + done.stderr).decode("utf-8", "replace").strip()
    return text[-300:]


def prepare(sc, dest, env=None):
    """A fresh copy of the project with the setup commands run: the original
    is never touched. Returns the hashes the `unchanged` checks compare
    against — taken after setup, so what setup changed is the baseline."""
    shutil.copytree(sc["workspace"], dest, ignore=skip,
                    ignore_dangling_symlinks=True)
    for cmd in sc.get("setup") or []:
        done = _shell(cmd, dest, env)
        if done.returncode != 0:
            raise ScenarioError(f"--setup falhou ({cmd}): " + _tail(done))
    return {rel: _sha(os.path.join(dest, rel))
            for chk in sc["check"] for rel in chk.get("unchanged") or []}


def preflight(sc, env=None):
    """Run the setup and the check commands once on an untouched copy, before
    a single token is spent. Raises ScenarioError when a check cannot even
    start (a wrong path would otherwise fail every paid run); returns
    warnings for checks that already pass with the task not done."""
    root = tempfile.mkdtemp(prefix="ctbench-pre-")
    workspace, answer = os.path.join(root, "w"), os.path.join(root, "answer.txt")
    try:
        prepare(sc, workspace, env)
        open(answer, "w").close()
        warnings = []
        for chk in sc["check"]:
            if "run" not in chk:
                continue
            done = _shell(chk["run"].replace("{answer}", answer), workspace, env)
            said = _tail(done)
            if done.returncode != 0 and NOT_FOUND.search(said):
                raise ScenarioError(f"--check {chk['run']!r} não chega a rodar: "
                                    + " ".join(said.split())[-220:])
            if done.returncode == 0:
                warnings.append(f"--check {chk['run']!r} já passa antes de a tarefa "
                                "ser feita: sozinho, não distingue feito de não feito")
        return warnings
    finally:
        shutil.rmtree(root, ignore_errors=True)


def save_changes(sc, workspace, dest, limit=20 * 1024 * 1024):
    """Keep what a run changed: every file that differs from the project's or
    is new, and the names of the ones it deleted. With these and the project,
    the checks can be run again later without paying for the model again."""
    changed, deleted, size = [], [], 0
    for dirpath, dirnames, filenames in os.walk(workspace):
        out = skip(dirpath, dirnames + filenames)
        dirnames[:] = [d for d in dirnames if d not in out]
        for name in filenames:
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, workspace)
            if name in out or _sha(full) == _sha(os.path.join(sc["workspace"], rel)):
                continue
            size += os.path.getsize(full)
            if size > limit:
                return {"dir": dest, "changed": changed, "deleted": deleted,
                        "truncated": True}
            target = os.path.join(dest, rel)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copyfile(full, target)
            changed.append(rel.replace(os.sep, "/"))
    for dirpath, dirnames, filenames in os.walk(sc["workspace"]):
        out = skip(dirpath, dirnames + filenames)
        dirnames[:] = [d for d in dirnames if d not in out]
        for name in filenames:
            rel = os.path.relpath(os.path.join(dirpath, name), sc["workspace"])
            if name not in out and not os.path.exists(os.path.join(workspace, rel)):
                deleted.append(rel.replace(os.sep, "/"))
    return {"dir": dest, "changed": changed, "deleted": deleted}


def restore_changes(changes, workspace):
    """Lay a run's saved changes over a fresh copy of the project."""
    for rel in changes.get("changed") or []:
        target = os.path.join(workspace, rel)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copyfile(os.path.join(changes["dir"], rel), target)
    for rel in changes.get("deleted") or []:
        target = os.path.join(workspace, rel)
        if os.path.exists(target):
            os.remove(target)


def run_checks(sc, workspace, answer, baseline, env=None):
    """Was the task done? One {"check", "ok", "detail"} per entry; the run
    passes only if all of them do."""
    out = []
    # Beside the working copy, not inside it: a checker may read the answer.
    answer_file = os.path.join(os.path.dirname(workspace), "answer.txt")
    with open(answer_file, "w", encoding="utf-8") as f:
        f.write(answer or "")
    for chk in sc["check"]:
        ok, label, detail = False, "?", ""
        try:
            if "unchanged" in chk:
                label = "unchanged " + ", ".join(chk["unchanged"])
                changed = [rel for rel in chk["unchanged"]
                           if _sha(os.path.join(workspace, rel)) != baseline.get(rel)]
                ok, detail = not changed, ("alterado: " + ", ".join(changed)
                                           if changed else "")
            elif "answer" in chk:
                label = f"expect /{chk['answer']}/"
                ok = bool(re.search(chk["answer"], answer or "", re.I | re.S))
                detail = "" if ok else "a resposta final não casa com a expressão"
            elif "run" in chk:
                label = "check " + chk["run"]
                done = _shell(chk["run"].replace("{answer}", answer_file),
                              workspace, env)
                ok = done.returncode == 0
                detail = "" if ok else _tail(done)
        except (OSError, subprocess.TimeoutExpired) as e:
            detail = f"{type(e).__name__}: {e}"
        out.append({"check": label, "ok": ok, "detail": detail})
    return out
