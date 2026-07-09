# claude-token

Saves and measures Claude Code tokens. When ON, every prompt you type is
rewritten by a cheap model (Haiku) into a precise English prompt before Claude
acts on it — vague prompts are the biggest source of wasted (output) tokens.
A statusline shows live context/cost, and `report` compares usage ON vs OFF.

## Requirements

- Python 3 (preinstalled on Linux/macOS)
- Claude Code installed and logged in (`claude` on PATH)

Nothing else — no pip packages.

## Setup

```bash
chmod +x claude-token
./claude-token on      # start a new Claude Code session after this
```

## Commands

```
on / off / status      toggle the optimizer
report [--days N]      tokens & cost, ON vs OFF
tips                   spot token-wasting config
```

## Notes

- Claude Code hooks can't replace your prompt, so the rewritten version is
  attached as context — same practical effect.
- Rewriting adds a few seconds and a tiny Haiku cost per prompt; it pays for
  itself on vague prompts and is skipped for short/long/command inputs.
- Everything it touches lives in `~/.claude/` (settings backup is created on
  first `on`). `off` removes it all. No background processes.
