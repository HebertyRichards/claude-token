"""Turn the optimizer on/off: installs/removes the hook, statusline,
/token slash command and PATH symlink; records ON periods and the baseline."""
import json
import os
import shutil

from common import (CLAUDE_DIR, ENTRY, HOME, MARKER, SETTINGS, STATE,
                    is_on, load_json, load_state, now_iso, save_json)
from usage import collect

BACKUP = SETTINGS + ".bak-claude-token"


def cmd_on():
    settings = load_json(SETTINGS, {})
    if os.path.exists(SETTINGS) and not os.path.exists(BACKUP):
        shutil.copy2(SETTINGS, BACKUP)

    ups = settings.setdefault("hooks", {}).setdefault("UserPromptSubmit", [])
    if not any(MARKER in json.dumps(h) for h in ups):
        ups.append({"hooks": [{"type": "command", "command": f"{ENTRY} hook"}]})
    settings["statusLine"] = {"type": "command", "command": f"{ENTRY} statusline"}
    save_json(SETTINGS, settings)

    st = load_state()
    ps = st.setdefault("periods", [])
    if not (ps and ps[-1].get("end") is None):
        ps.append({"start": now_iso(), "end": None})
    # Baselines: avg per response while OFF — the statusline uses them to
    # show what the session would look like without ECO.
    off = collect(30)["off"]
    if off["turns"]:
        st["baseline_cost"] = off["cost"] / off["turns"]
        st["baseline_out"] = off["out"] / off["turns"]
        st["baseline_read"] = (off["inp"] + off["cr"] + off["cw"]) / off["turns"]
    save_json(STATE, st)

    install_slash_command()
    install_symlink()

    print("LIGADO — prompts serão reescritos pelo Haiku; statusline com uso ao vivo.")
    print("Vale a partir da próxima sessão do Claude Code.")


def cmd_off():
    settings = load_json(SETTINGS, {})
    hooks = settings.get("hooks", {})
    kept = [h for h in hooks.get("UserPromptSubmit", []) if MARKER not in json.dumps(h)]
    if kept:
        hooks["UserPromptSubmit"] = kept
    else:
        hooks.pop("UserPromptSubmit", None)
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
    ps = load_state().get("periods", [])
    if ps:
        p = ps[-1]
        print(f"último período: {p['start']} -> {p.get('end') or 'agora'}")


def install_slash_command():
    # A slash command is just a markdown file — any user can add one.
    cmd_dir = os.path.join(CLAUDE_DIR, "commands")
    os.makedirs(cmd_dir, exist_ok=True)
    with open(os.path.join(cmd_dir, "token.md"), "w", encoding="utf-8") as f:
        f.write(
            "---\n"
            "description: Economia de tokens — status, report, cache, index, tips, on/off\n"
            "argument-hint: [status|report|cache|index|search|tips|on|off]\n"
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
    if os.path.isdir(bin_dir) and not os.path.exists(link):
        try:
            os.symlink(ENTRY, link)
        except OSError:
            pass
