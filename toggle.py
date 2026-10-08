"""Turn the optimizer on/off: installs/removes the hooks, statusline,
/token slash command and PATH symlink, and records ON periods."""
import json
import os
import shutil

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

    for event, sub in (("UserPromptSubmit", "hook"), ("PostToolUse", "posttool")):
        entries = hooks.setdefault(event, [])
        if not any(MARKER in json.dumps(h) for h in entries):
            entries.append(
                {"hooks": [{"type": "command", "command": f"{COMMAND} {sub}"}]})
    settings["statusLine"] = {"type": "command", "command": f"{COMMAND} statusline"}
    save_json(SETTINGS, settings)

    st = load_state()
    ps = st.setdefault("periods", [])
    if not (ps and ps[-1].get("end") is None):
        ps.append({"start": now_iso(), "end": None})
    st.setdefault("level", DEFAULT_LEVEL)
    save_json(STATE, st)

    install_slash_command()
    install_symlink()

    print(f"LIGADO no nível {level()} — {LEVELS[level()]}")
    print("Troque com `claude-token level N` (ou /token level N).")
    print("Vale a partir da próxima sessão do Claude Code.")


def cmd_off():
    settings = load_json(SETTINGS, {})
    hooks = settings.get("hooks", {})
    for event in ("UserPromptSubmit", "SessionEnd", "PostToolUse"):
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
    print("DESLIGADO — hook e statusline removidos.")


def cmd_status():
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
            "description: Economia de tokens — status, level, memory, sessions, report, cache, index, recall, toon, tips, on/off\n"
            "argument-hint: [status|level|memory|sessions|recall|report|cache|index|search|toon|tips|on|off]\n"
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
