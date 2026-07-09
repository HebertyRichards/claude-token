# claude-token

Saves and measures Claude Code tokens. Pure Python stdlib, no dependencies,
no background processes, nothing leaves your machine (the only model calls go
through your own `claude` CLI).

## What it does

**Prompt rewriting** — when ON, every prompt you type is rewritten by a cheap
model (Haiku) into one precise, minimal-scope English prompt before Claude
acts on it. Vague prompts are the biggest source of wasted tokens: they
inflate the OUTPUT, which costs 5x more than input. The rewriter reads the
project's `CLAUDE.md`/`AGENTS.md` so the optimized prompt already follows the
project's conventions. Non-task messages (questions, remarks) are just
translated. Skipped for slash commands, very short and very long prompts.

**Model routing** — plain questions ("what does X mean?") skip the rewrite
and get a directive for a brief, tool-free answer, plus a reminder that
`/model haiku` is ~5x cheaper for question-only sessions.

**Context pruning nudge** — when the context passes 70%, a note tells Claude
to stop rereading files and to suggest `/compact` (or `/clear`) to you.

**RAG (BM25) search** — `index` builds a local lexical index of the project
(40-line chunks, camelCase/snake_case aware). With an index present, each
prompt gets the top matching `file:start-end` ranges injected, so Claude reads
snippets instead of dumping whole files into context. Offline, no embeddings.

**Session digests + recall** — when a session ends (SessionEnd hook), sessions
worth remembering (>50 KB) are summarized by Haiku into
`~/.claude/claude-token-digests/`. `recall <query>` searches those digests and
the raw transcripts of the last 60 days at zero token cost — instead of
resuming a heavy chat (which reloads the whole history, uncached, full price)
you take the useful snippet into a fresh session.

**TOON / log compactor** — `toon` shrinks what you're about to paste into a
prompt. A JSON payload with a uniform listing becomes TOON (fields declared
once, then values only — ~40-50% smaller); other JSON is minified; log text
(console.log, stack traces, prints) gets ANSI codes stripped and consecutive
duplicate lines collapsed into `line  [xN]`. This must happen before pasting —
no hook can rewrite pasted content.

```
$ claude-token toon hoteis.json
hoteis[4]{id,nome,estrelas,preco,disponivel,cidade}:
 101,Hotel Copacabana,5,890.5,true,Rio de Janeiro
 102,Pousada do Sol,3,210.0,true,Búzios
 ...
[TOON] ~111 -> ~57 tokens (49% smaller) — paste the output above into the prompt.
```

The token estimate goes to stderr, so stdout pipes clean to the clipboard
(`claude-token toon data.json | xclip -selection c`). Everything it prints is
English — its output is destined for the model's context, not just your eyes.

**Statusline** — live in the terminal, short lines that fit small screens:

```
ECO✅ ON (/token off desliga) | Fable 5 | ctx 54.1k/200k (27%) 🟢 | cache🔥
leitura: com ECO 543.8k · sem ECO ~1.8M · você economizou 1.2M ✔
saída:   com ECO 11.5k · sem ECO ~12.9k · você economizou 1.4k ✔
custo:   com ECO $0.86 · sem ECO ~$1.39 · você economizou $0.53 ✔
```

- `ctx` — context fill; 🟢 <60%, 🟡 <85%, 🔴 above.
- `cache🔥/🧊` — whether the prompt cache is being reused (cached input costs
  10%). Flips to 🧊 after 5 min idle (the cache TTL) or right after a compact.
- The "sem ECO" columns come from a baseline: your average per-response
  read/output/cost over the last 30 days of OFF usage, captured at `on` time.
- `/compact` resets everything (context, totals, cost) — the statusline counts
  only what came after the last compact boundary. `/clear` starts a fresh
  transcript, so it resets naturally.

**Reports** — `report` compares tokens and cost ON vs OFF over real transcript
data (costs are estimates from public per-MTok prices). `cache` shows prompt
cache hit rate, cold turns and what they cost extra. `tips` audits config
waste: oversized `CLAUDE.md` files, MCP servers inflating every session.

## Requirements

- Python 3 (preinstalled on Linux/macOS)
- Claude Code installed and logged in (`claude` on PATH)

Nothing else — no pip packages.

## Setup

```bash
chmod +x claude-token
./claude-token on      # start a new Claude Code session after this
```

`on` registers the hooks and statusline in `~/.claude/settings.json` (a backup
is created first), installs the `/token` slash command and a
`~/.local/bin/claude-token` symlink. `off` removes it all.

## Commands

```
on / off / status      toggle the optimizer
report [--days N]      tokens & cost, ON vs OFF (default 30 days)
cache  [--days N]      prompt cache efficiency and cold-turn waste (default 7)
index  [path]          build the RAG index for a project (rerun after big changes)
search <query>         BM25 search over the indexed project
recall <query>         search past sessions: digests first, then transcripts
toon   [file]          compact JSON/logs before pasting (also reads stdin)
tips                   spot token-wasting config
```

Inside Claude Code: `/token <anything above>`, e.g. `/token report`.

## Files it owns (all under `~/.claude/`)

```
claude-token-state.json    ON/OFF periods + OFF baselines
claude-token-rag/          one BM25 index per project
claude-token-digests/      one markdown briefing per finished session
commands/token.md          the /token slash command
settings.json.bak-claude-token   backup taken on first `on`
```

## Notes

- Claude Code hooks can't replace your prompt, so the rewritten version is
  attached as context with a "follow it" directive — same practical effect.
- Rewriting adds a few seconds and a tiny Haiku cost per prompt; it pays for
  itself on vague prompts. Digesting a session costs one short Haiku call.
- Everything injected for the model is written in English (tokenizes ~20%
  cheaper than Portuguese); everything printed for you is in Portuguese.
- All costs shown are ESTIMATES computed from transcript usage and public
  per-MTok prices — your bill/plan is the source of truth.
