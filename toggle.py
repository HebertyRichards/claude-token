"""Turn the optimizer on/off: installs/removes the hooks, statusline,
/token slash command and PATH symlink, and records ON periods."""
import json
import os
import shutil
import subprocess

from common import (CLAUDE_DIR, COMMAND, DEFAULT_LEVEL, ENTRY, HOME, LEVELS, MARKER,
                    SETTINGS, STATE, is_on, level, load_json, load_state,
                    now_iso, save_json, set_level)

BACKUP = SETTINGS + ".bak-claude-token"


def cmd_on():
    settings = load_json(SETTINGS, {})
    if os.path.exists(SETTINGS) and not os.path.exists(BACKUP):
        shutil.copy2(SETTINGS, BACKUP)

    hooks = settings.setdefault("hooks", {})
    # SessionEnd is gone: the Haiku digest that used it was deleted. Drop a
    # leftover registration from an older install, but leave other people's
    # SessionEnd hooks alone.
    kept = [h for h in hooks.get("SessionEnd", []) if MARKER not in json.dumps(h)]
    if kept:
        hooks["SessionEnd"] = kept
    else:
        hooks.pop("SessionEnd", None)

    for event, sub, matcher in (("UserPromptSubmit", "hook", None),
                                ("PostToolUse", "posttool", None),
                                ("PreToolUse", "skillguard", "Skill")):
        entries = hooks.setdefault(event, [])
        if not any(MARKER in json.dumps(h) for h in entries):
            entry = {"hooks": [{"type": "command", "command": f"{COMMAND} {sub}"}]}
            if matcher:
                entry = {"matcher": matcher, **entry}
            entries.append(entry)
    # Someone else's status line (a company one, another tool) stays: replacing
    # it silently is exactly the kind of conflict this tool must not cause.
    current = settings.get("statusLine")
    if not current or MARKER in json.dumps(current):
        settings["statusLine"] = {"type": "command", "command": f"{COMMAND} statusline"}
    else:
        print("AVISO: já existe outra statusline em settings.json; mantive a dela.")
    save_json(SETTINGS, settings)

    st = load_state()
    ps = st.setdefault("periods", [])
    if not (ps and ps[-1].get("end") is None):
        ps.append({"start": now_iso(), "end": None})
    st.setdefault("level", DEFAULT_LEVEL)
    save_json(STATE, st)

    install_slash_command()
    install_symlink()
    install_plugin()
    t3_meters(True)

    print(f"LIGADO no nível {level()} — {LEVELS[level()]}")
    print("Troque com `claude-token level N` (ou /token level N).")
    print("Vale a partir da próxima sessão do Claude Code.")


def cmd_off():
    settings = load_json(SETTINGS, {})
    hooks = settings.get("hooks", {})
    for event in ("UserPromptSubmit", "SessionEnd", "PostToolUse", "PreToolUse"):
        kept = [h for h in hooks.get(event, []) if MARKER not in json.dumps(h)]
        if kept:
            hooks[event] = kept
        else:
            hooks.pop(event, None)
    if not hooks:
        settings.pop("hooks", None)
    if MARKER in json.dumps(settings.get("statusLine", {})):
        settings.pop("statusLine", None)
    save_json(SETTINGS, settings)

    st = load_state()
    ps = st.get("periods", [])
    if ps and ps[-1].get("end") is None:
        ps[-1]["end"] = now_iso()
        save_json(STATE, st)
    uninstall_plugin()
    t3_meters(False)
    print("DESLIGADO — hooks, statusline e skills do plugin removidos.")


def cmd_status():
    # Inside a session (/token), the statusline first: T3 Code and other SDK
    # hosts never draw it, so this is the only place it shows there.
    from statusline import current_lines
    lines = current_lines()
    if lines:
        print("\n".join(lines) + "\n")
    print("LIGADO" if is_on() else "DESLIGADO")
    print(f"nível {level()}: {LEVELS[level()]}")
    ps = load_state().get("periods", [])
    if ps:
        p = ps[-1]
        print(f"último período: {p['start']} -> {p.get('end') or 'agora'}")


def cmd_level(argv):
    """Read or set how much the harness injects."""
    if not argv:
        print(f"nível atual: {level()}")
        for n, desc in LEVELS.items():
            print(f"  {n}  {desc}")
        print(f"\nuso: claude-token level <0-{max(LEVELS)}>")
        print("Nenhum nível chama modelo — é só injeção de texto, ~150 a 350")
        print("tokens por sessão. O antigo nível 3 (reescrita do prompt pelo")
        print("Haiku) foi removido: custava ~17,7k tokens por prompt e alterava")
        print("o que você pediu no caminho.")
        return
    try:
        n = int(argv[0])
    except ValueError:
        n = -1
    if n not in LEVELS:
        print(f"nível inválido: {argv[0]} (use {', '.join(map(str, LEVELS))})")
        return
    set_level(n)
    print(f"nível {n}: {LEVELS[n]}")
    print("Vale a partir do próximo prompt.")


def install_slash_command():
    # A slash command is just a markdown file — any user can add one.
    cmd_dir = os.path.join(CLAUDE_DIR, "commands")
    os.makedirs(cmd_dir, exist_ok=True)
    with open(os.path.join(cmd_dir, "token.md"), "w", encoding="utf-8") as f:
        f.write(
            "---\n"
            "description: Economia de tokens — status, level, memory, sessions, report, cache, index, recall, toon, tips, skills, on/off\n"
            "argument-hint: [status|level|memory|sessions|recall|report|cache|index|search|toon|tips|skills|on|off]\n"
            "allowed-tools: Bash(claude-token:*)\n"
            "---\n\n"
            "!`claude-token $ARGUMENTS`\n\n"
            # This line is read by the model — English tokenizes cheaper.
            "Show the user the output above exactly as is; do not reformat "
            "or summarize.\n"
        )


def install_symlink():
    # Lets `claude-token on/off` work from any directory.
    bin_dir = os.path.join(HOME, ".local", "bin")
    link = os.path.join(bin_dir, "claude-token")
    if os.name == "nt":
        # Symlinks need admin on Windows; write shims instead (sh for the
        # Git Bash that /token runs in, .cmd for cmd/PowerShell).
        if os.path.isdir(bin_dir):
            with open(link, "w", encoding="utf-8", newline="\n") as f:
                f.write(f'#!/bin/sh\nexec {COMMAND} "$@"\n')
            with open(link + ".cmd", "w", encoding="utf-8") as f:
                f.write(f"@{COMMAND} %*\n")
        return
    if os.path.isdir(bin_dir) and not os.path.exists(link):
        try:
            os.symlink(ENTRY, link)
        except OSError:
            pass


PLUGIN_ID = "token-lean@token-lean"


def _claude(*args):
    """Runs `claude plugin ...`; (ok, output). Never raises."""
    try:
        r = subprocess.run(["claude", "plugin", *args], capture_output=True,
                           text=True, timeout=180)
        return r.returncode == 0, (r.stdout + r.stderr).strip()
    except (OSError, subprocess.SubprocessError) as e:
        return False, str(e)


def install_plugin():
    # The skills ship as a plugin so their names come prefixed
    # (token-lean:bugfix) and can never overwrite, or be overwritten by,
    # a skill with the same name in ~/.claude/skills or in a project.
    repo = os.path.dirname(ENTRY)
    ok, out = _claude("marketplace", "add", repo)
    if not ok and "already" not in out.lower():
        print(f"AVISO: não registrei o marketplace local do plugin: {out[-300:]}")
        return
    ok, out = _claude("install", PLUGIN_ID)
    if ok and "already" in out.lower():
        ok, out = _claude("update", PLUGIN_ID)
    if ok:
        print("skills instaladas: token-lean:bugfix, :feature, :frontend, :security, :backend, :tests, :infra, :cd")
    else:
        print(f"AVISO: o plugin com as skills não instalou: {out[-300:]}")


def uninstall_plugin():
    _claude("uninstall", PLUGIN_ID)
    _claude("marketplace", "remove", "token-lean")


# T3 Code runs Claude Code through the SDK and never draws the statusline. It
# has a context meter and per-thread usage of its own, both off by default:
# `on` turns them on, `off` turns back off only the ones `on` turned on.
T3_SETTINGS = os.path.join(HOME, ".t3", "userdata", "client-settings.json")
T3_METERS = ("contextWindowMeterEnabled", "threadDetailsUsageEnabled")


def t3_meters(enable):
    t3 = load_json(T3_SETTINGS, None)
    if not isinstance(t3, dict):
        return  # no T3 Code on this machine
    st = load_state()
    if enable:
        flipped = [k for k in T3_METERS if t3.get(k) is not True]
        if not flipped:
            return
        for k in flipped:
            t3[k] = True
        st["t3_meters"] = sorted(set(st.get("t3_meters", [])) | set(flipped))
        print("T3 Code: medidor de contexto e uso por thread ligados "
              "(a statusline não aparece lá; /token status mostra as mesmas linhas).")
    else:
        flipped = [k for k in st.pop("t3_meters", []) if t3.get(k) is True]
        if not flipped:
            save_json(STATE, st)
            return
        for k in flipped:
            t3[k] = False
    tmp = T3_SETTINGS + ".tmp"
    try:
        # T3 keeps this file compact, on one line: written the same way.
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(t3, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, T3_SETTINGS)
    except OSError as e:
        print(f"AVISO: não consegui mudar as configurações do T3 Code: {e}")
        return
    save_json(STATE, st)
