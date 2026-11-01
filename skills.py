"""Skill arbitration: claude-token ships its own bugfix/feature/frontend skills
(as the `token-lean` plugin, so their names are prefixed and never collide).
When another installed skill does the same job, a PreToolUse hook on the Skill
tool stops the first call once per session and has Claude ask the user which
one leads that kind of work in THIS project — projects differ in business
rules and layout, so the choice is stored per project, like the compact note.

It only ever steps in for groups where a claude-token skill competes: skills
that don't overlap with ours are never touched. And it fails open: a second
call to the same skill in the same session always passes, so a session that
can't ask (claude -p) or a skill that does a different job is never blocked."""
import glob
import json
import os
import re
import sys

from common import CLAUDE_DIR, level, load_json, now_iso, save_json

PREFS = os.path.join(CLAUDE_DIR, "claude-token-skills.json")
# Plugin names starting with "claude-" are reserved by Claude Code.
PLUGIN = "token-lean"
OWN = {g: f"{PLUGIN}:{g}" for g in ("bugfix", "feature", "frontend", "security", "backend")}
ALL = "all"

# A skill joins a group at score >= 3. Name hit: 3. Description: only its
# capability sentence counts (the first one) — "Use when ... or debugging X"
# further on is a side use, and specialists all mention debugging. Strong
# phrases count 2, weak ones 1. Kept conservative on purpose: a false
# competitor costs the user a needless question.
GROUPS = {
    "bugfix": (("bug", "debug", "red-green", "troubleshoot"),
               ("bug", "root cause", "stack trace", "troubleshoot",
                "traceback", "error message"),
               ("debug", "regression", "flaky")),
    "feature": (("feature", "requirements"),
                ("new feature", "feature spec", "user stories",
                 "acceptance criteria", "requirements", "multi-file",
                 "invented api", "placeholder code"),
                ("features",)),
    "frontend": (("frontend", "front-end", "mobile-first", "layout", "ui-",
                  "-ui", "impeccable"),
                 ("frontend", "front-end", "layout", "responsive",
                  "user interface", "landing page", "design system",
                  "redesign", "mobile-first", "mobile first", "web interface"),
                 ("css",)),
    "security": (("security", "secure-code", "pen-test", "pentest", "owasp"),
                 ("vulnerab", "owasp", "penetration", "idor", "csrf", "xss",
                  "injection", "security review", "security audit"),
                 ("security", "hardening", "auth")),
    "backend": (("backend", "back-end"),
                ("backend", "back-end", "server-side", "microservices"),
                ("rest api", "api", "endpoint", "database")),
}


# What a prompt looks like for each group (pt + en), for the routing line.
PROMPT_WORDS = {
    "bugfix": ("bug", "erro", "error", "exception", "traceback", "crash",
               "quebrou", "quebrado", "não funciona", "nao funciona", "falha",
               "corrige", "corrija", "conserta", "fix "),
    "feature": ("feature", "funcionalidade", "implementa", "implemente",
                "novo endpoint", "nova rota", "adicione", "adicionar",
                "implement", "new endpoint"),
    "frontend": ("layout", "tela", "página", "pagina", "css", "responsiv",
                 "mobile", "componente", "botão", "botao", "frontend",
                 "front-end", "interface", "design"),
    "security": ("segurança", "seguranca", "security", "vulnerab", "owasp",
                 "csrf", "xss", "idor", "injection", "injeção", "pentest"),
    "backend": ("backend", "back-end", "endpoint", "rota", "api ", "query",
                "migration", "migração", "service", "repositório"),
}


# --- discovery -------------------------------------------------------------

def frontmatter(path):
    """name/description from a SKILL.md header (folded and quoted values too)."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read(6000)
    except OSError:
        return {}
    if not text.startswith("---"):
        return {}
    data, key = {}, None
    for line in text.splitlines()[1:]:
        if line.strip() == "---":
            break
        m = re.match(r"^([A-Za-z_-]+):\s*(.*)$", line)
        if m:
            key, val = m.group(1), m.group(2).strip()
            data[key] = "" if val in (">", "|", ">-", "|-") else val.strip("\"'")
        elif key and line[:1] in (" ", "\t"):
            data[key] = (data[key] + " " + line.strip()).strip()
    return data


def project_root(cwd):
    d = os.path.abspath(cwd or os.getcwd())
    while True:
        if os.path.exists(os.path.join(d, ".git")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            return os.path.abspath(cwd or os.getcwd())
        d = parent


def installed_plugins():
    """{plugin_name: (marketplace, install_path)} from Claude Code's registry."""
    reg = load_json(os.path.join(CLAUDE_DIR, "plugins", "installed_plugins.json"), {})
    out = {}
    for pid, installs in (reg.get("plugins") or {}).items():
        name, _, mkt = pid.partition("@")
        for inst in installs if isinstance(installs, list) else [installs]:
            path = (inst or {}).get("installPath")
            if path:
                out[name] = (mkt, path)
    return out


def own_installed():
    return PLUGIN in installed_plugins()


def _skill(path, name, origin):
    fm = frontmatter(path)
    return {"name": name or fm.get("name") or os.path.basename(os.path.dirname(path)),
            "description": fm.get("description", ""), "origin": origin}


def discover(root):
    """Every skill on disk this session can see, except claude-token's own."""
    found = {}

    def add(s):
        found.setdefault(s["name"], s)

    for p in sorted(glob.glob(os.path.join(root, ".claude", "skills", "*", "SKILL.md"))):
        add(_skill(p, None, "do projeto (.claude/skills)"))
    user = os.path.join(CLAUDE_DIR, "skills")
    for pattern in ("*", "*/*", "*/*/*"):
        for p in sorted(glob.glob(os.path.join(user, pattern, "SKILL.md"))):
            d = os.path.dirname(p)
            if os.path.exists(os.path.join(d, ".dev-setup")):
                origin = "dev-setup"
            elif f"{os.sep}synced{os.sep}" in p:
                origin = "sincronizada (claude.ai)"
            else:
                origin = "pessoal (~/.claude/skills)"
            add(_skill(p, None, origin))
    for plugin, (mkt, path) in installed_plugins().items():
        if plugin == PLUGIN:
            continue
        for p in sorted(glob.glob(os.path.join(path, "skills", "*", "SKILL.md"))):
            fm_name = frontmatter(p).get("name") or os.path.basename(os.path.dirname(p))
            add(_skill(p, f"{plugin}:{fm_name}", f"plugin {plugin}@{mkt}"))
    return list(found.values())


def capability(description):
    """The description's first sentence: what the skill IS, not every use."""
    return re.split(r"(?<=[.!?])\s", description.strip(), maxsplit=1)[0].lower()


def _hits(text, phrases):
    # Prefix boundary only: "bug" must not match inside "debug", but
    # "debug" still matches "debugging".
    return sum(bool(re.search(r"(?<![a-z])" + re.escape(p), text)) for p in phrases)


# Skills about skills (auditing or writing them) are tooling, not app work.
NOT_APP = ("skill-", "-skill", "write-a-skill")


def classify(name, description):
    n, cap = name.lower(), capability(description)
    groups = set()
    if any(k in n for k in NOT_APP):
        return groups
    for g, (name_kw, strong, weak) in GROUPS.items():
        score = (3 * any(k in n for k in name_kw)
                 + 2 * _hits(cap, strong) + _hits(cap, weak))
        if score >= 3:
            groups.add(g)
    return groups


def members(group, skills):
    """claude-token's skill for the group plus every other skill that does it."""
    return [{"name": OWN[group], "origin": "claude-token"}] + [
        s for s in skills if group in classify(s["name"], s["description"])]


# --- preferences -----------------------------------------------------------

def prefs_for(root):
    return load_json(PREFS, {}).get("projects", {}).get(root, {})


def set_pref(root, group, lead):
    data = load_json(PREFS, {})
    proj = data.setdefault("projects", {}).setdefault(root, {})
    if lead is None:
        proj.pop(group, None)
    else:
        proj[group] = {"lead": lead, "at": now_iso()}
    save_json(PREFS, data)


def nudge_once(session_id, key):
    """True the first time a key is seen in a session (then remembers it)."""
    data = load_json(PREFS, {})
    seen = data.setdefault("nudged", {})
    keys = seen.setdefault(session_id or "?", [])
    if key in keys:
        return False
    keys.append(key)
    if len(seen) > 30:
        data["nudged"] = dict(list(seen.items())[-30:])
    save_json(PREFS, data)
    return True


# --- explicit choice -------------------------------------------------------

def last_user_text(transcript, size=65536):
    """The user's last typed message (tool results are user records too)."""
    try:
        with open(transcript, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - size))
            lines = f.read().decode("utf-8", "replace").splitlines()
    except (OSError, TypeError):
        return ""
    for line in reversed(lines):
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("type") != "user":
            continue
        content = (rec.get("message") or {}).get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            texts = [c.get("text", "") for c in content
                     if isinstance(c, dict) and c.get("type") == "text"]
            if texts:
                return "\n".join(texts)
    return ""


def named_by_user(skill, text):
    """The user picked this skill themselves: `$name`, `/name`, or its full
    name. A bare short name like `bugfix` is too common a word to count."""
    if not text:
        return False
    short = skill.split(":")[-1]
    pats = [re.escape(skill)] + [r"[$/]" + re.escape(short)]
    if "-" in short:
        pats.append(re.escape(short))
    return any(re.search(r"(?<![\w-])" + p + r"(?![\w-])", text, re.I) for p in pats)


# --- the hook --------------------------------------------------------------

def _label(s):
    return f"{s['name']} ({s['origin']})"


def decide(skill, root, session_id, user_text, skills=None, own=None):
    """The deny reason for this Skill call, or None to let it through."""
    if own is None:
        own = own_installed() or skill in OWN.values()
    if not own:
        return None  # our skills aren't loaded: nothing of ours competes
    skills = discover(root) if skills is None else skills
    if skill in OWN.values():
        groups = {g for g, s in OWN.items() if s == skill}
    else:
        me = next((s for s in skills if s["name"] == skill), None)
        groups = classify(me["name"], me["description"]) if me else set()
    if not groups or named_by_user(skill, user_text):
        return None
    prefs = prefs_for(root)
    for g in sorted(groups):
        group = members(g, skills)
        names = [s["name"] for s in group]
        if len(group) < 2:
            continue
        lead = (prefs.get(g) or {}).get("lead")
        if lead == ALL or lead == skill:
            continue
        if lead not in names:
            lead = None  # the chosen skill was uninstalled: ask again
        if not nudge_once(session_id, f"{g}:{skill}"):
            continue  # fail open: the second call in a session passes
        if lead is None:
            opts = " · ".join(_label(s) for s in group[:4])
            return (
                f"[claude-token] More than one installed skill does {g} work: "
                f"{opts}. This project ({root}) has not chosen one yet, and "
                "projects differ in business rules and layout. Ask the user "
                "(AskUserQuestion; if that tool is unavailable or fails, a "
                "short plain question, then stop) which skill should lead "
                f"{g} work in this project (up to 3 skills plus an option "
                f"'all: no preference'), name each option's origin, then run "
                f"`claude-token skills prefer {g} <skill|all>` and invoke the "
                "chosen skill. If the user cannot be asked, invoke the skill "
                "again: it will pass.")
        return (
            f"[claude-token] In this project the user chose {lead} to lead "
            f"{g} work. Invoke {lead} instead. If {skill} does a different "
            f"job here (not {g} itself), invoke it again: it will pass. To "
            f"change the choice: `claude-token skills prefer {g} <skill|all>`.")
    return None


def cmd_skillguard():
    """PreToolUse hook on the Skill tool. Silent on any error (fail open)."""
    try:
        if level() <= 0:
            return
        data = json.load(sys.stdin)
        if data.get("tool_name") != "Skill":
            return
        skill = ((data.get("tool_input") or {}).get("skill") or "").lstrip("/")
        if not skill:
            return
        root = project_root(data.get("cwd"))
        reason = decide(skill, root, data.get("session_id"),
                        last_user_text(data.get("transcript_path")))
    except Exception:
        return
    if reason:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse", "permissionDecision": "deny",
            "permissionDecisionReason": reason}}))


def prompt_groups(prompt):
    p = prompt.lower()
    return [g for g, words in PROMPT_WORDS.items() if any(w in p for w in words)]


def routing_note(cwd, prompt):
    """One line for the UserPromptSubmit hook: the project's chosen skill for
    this kind of task. Only after the user chose one; never a guess."""
    root = project_root(cwd)
    prefs = prefs_for(root)
    leads = {g: (prefs.get(g) or {}).get("lead") for g in prompt_groups(prompt)}
    leads = {g: s for g, s in leads.items() if s and s != ALL}
    if not leads:
        return ""
    # Only now pay for a disk scan: a choice whose skill was uninstalled is
    # dropped here, and the Skill hook asks again when it comes up.
    present = set(OWN.values()) | {s["name"] for s in discover(root)}
    lines = [f"{g} → {s}" for g, s in leads.items() if s in present]
    if not lines:
        return ""
    return ("[claude-token] Skills the user chose for this project: "
            + "; ".join(lines) + ". Use them when the task matches.")


# --- CLI -------------------------------------------------------------------

def cmd_skills(argv):
    path = os.getcwd()
    if "--path" in argv:
        i = argv.index("--path")
        if i + 1 < len(argv):
            path = argv[i + 1]
        argv = argv[:i] + argv[i + 2:]
    root = project_root(path)
    sub = argv[0] if argv else "list"

    if sub == "prefer" and len(argv) >= 3:
        g, choice = argv[1], argv[2]
        if g not in OWN:
            print(f"grupo inválido: {g} (use {', '.join(OWN)})")
            return
        names = [s["name"] for s in members(g, discover(root))]
        if choice in ("todas", "all"):
            choice = ALL
        elif choice not in names:
            print(f"'{choice}' não faz {g} neste projeto. Opções: "
                  + ", ".join(names) + ", all")
            return
        set_pref(root, g, choice)
        shown = "todas, sem preferência" if choice == ALL else choice
        print(f"{root}\n  {g}: {shown}")
        return
    if sub == "reset":
        for g in (argv[1:2] or list(OWN)):
            set_pref(root, g, None)
        print(f"escolhas apagadas em {root}: {', '.join(argv[1:2] or OWN)}")
        return
    if sub != "list":
        print("uso: claude-token skills [prefer <grupo> <skill|all> | reset [grupo]] [--path P]")
        return

    skills = discover(root)
    prefs = prefs_for(root)
    print(f"projeto: {root}")
    if not own_installed():
        print("(plugin token-lean, com as skills do claude-token, não instalado: rode `claude-token on`)")
    for g in OWN:
        group = members(g, skills)
        lead = (prefs.get(g) or {}).get("lead")
        chosen = ("todas" if lead == ALL else lead) if lead else "ainda não escolhida"
        print(f"\n{g}: {chosen}")
        for s in group:
            mark = "→" if s["name"] == lead else " "
            print(f"  {mark} {_label(s)}")
        if len(group) < 2:
            print("    (sem concorrente: nada a perguntar)")
