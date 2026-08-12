# claude-token

Saves and measures Claude Code tokens. Pure Python stdlib, no dependencies, no
background processes, nothing leaves your machine — and, since the prompt
rewriter was removed, **no model calls at all**. Every feature here is either
local text injection or local file reading.

## What it does

**Lean-output rules** — once per session, scoped to the project's real stack
(a TS frontend gets vitest/playwright hints, not pytest): quiet test
reporters, no raw API/HTML dumps, long output to a file then read its tail,
PDFs by narrow page range, no re-capturing screenshots. Plus a rule to state
the real scope *after looking at the code* and ask you which model to use —
never to guess the size of a task from the request alone.

**Oversized-result watcher** — a `PostToolUse` hook flags any tool result over
~5k tokens and names the specific fix (`| jq`, quiet reporter, `| claude-token
toon`, redirect-and-tail). Reactive but deterministic: it fires no matter what
the model forgot.

**Compact memory** — `/compact` empties the model's context, not the
transcript: the discarded turns stay on disk in the same `.jsonl`. So after a
compact the hook injects a ~50-token pointer saying what was recovered — your
requests verbatim, files edited, commits, tests, and the **uncommitted** git
state — and asks you whether to load the full note. Rebuilt mechanically: no
model call, nothing summarized, nothing rewritten.

**RAG (BM25) search** — `index` builds a local lexical index of the project
(40-line chunks, camelCase/snake_case aware). With an index present, each
prompt gets the top matching `file:start-end` ranges injected, so Claude reads
snippets instead of dumping whole files into context. Offline, no embeddings.

**Session lookup by id** — `sessions` lists past conversations, `recall
<query>` finds which ones mention something, `memory <id>` prints what was
done in one. Zero token cost: it is all reading files you already have.
Instead of resuming a heavy chat (which reloads the whole history, uncached,
at full price), take the useful piece into a fresh session.

**TOON / paste compactor** — `toon` shrinks what you're about to paste:

| input | result |
|---|---|
| JSON with a uniform listing | TOON — fields declared once, then values only |
| XML with repeated records | TOON (`XML -> TOON (3 <book> records)`) |
| other XML | minified, and labeled as minified |
| HTML | DOM stripped to structure/selectors (`--keep script,css` preserves what you're analyzing) |
| `.docx` | unzipped to plain text (stdlib `zipfile`) |
| logs / text | ANSI stripped, repeated lines collapsed into `line [xN]` |
| PDF, xlsx, pptx, binary | **refused**, with the useful alternative |

```
$ claude-token toon catalogo.xml
book[3]{id,title,price}:
 1,Um,10
 2,Dois,20
 3,Tres,30
[XML -> TOON (3 <book> records)] ~54 -> ~13 tokens (75% smaller)
```

Stats go to stderr so stdout pipes clean (`claude-token toon data.json | xclip
-selection c`). This has to happen **before** pasting: a hook can add context
but cannot rewrite what you already pasted.

**Statusline** — live in the terminal, short lines that fit small screens:

```
ECO✅ nv2 (/token level N) | Opus 5 | ctx 54.1k/200k (27%) 🟢 | cache🔥
uso do plano: 5h 34% 🟢 · semana 12% 🟢
sessão (48 resp): leitura 543.8k · saída 11.5k · custo ~$0.86
```

- `ctx` — context fill; 🟢 <60%, 🟡 <85%, 🔴 above.
- `cache🔥/🧊` — whether the prompt cache is being reused (cached input costs
  10%). Flips to 🧊 after 5 min idle (the cache TTL) or right after a compact.
- On a subscription the **plan window**, not the dollar figure, is what
  actually runs out — so it gets the headline line when the harness reports it.
- `/compact` resets the session counters (they count only what came after the
  last compact boundary). `/clear` starts a fresh transcript.

**Reports** — `report` gives straight totals from the transcripts. `cache`
shows prompt cache hit rate, cold turns and what they cost extra. `tips`
audits config waste: oversized `CLAUDE.md` files, MCP servers inflating every
session.

## Levels

How much the harness injects. No level makes a model call — it is text
injection only, ~150-350 tokens per session.

```
0  off     nothing injected
1  lean    output/scope rules + big-result watcher + compact memory
2  normal  + RAG and the context-full warning        (default)
```

`claude-token level N`, or `/token level N`.

There used to be a level 3 that had Haiku rewrite every prompt into English.
It was removed: measured over 135 runs it cost **~17.7k input tokens per
prompt** to save perhaps 40, and — being a model in the middle — it could and
did alter what the user actually asked. Your prompt now reaches the model
exactly as you wrote it.

## On honest measurement

This tool refuses to flatter itself, because a savings number you can't trust
is worse than none:

- **No ON-vs-OFF comparison.** The optimizer is meant to stay on, so there is
  no matched off-period to compare against. A per-turn average taken from an
  unrelated stretch of past work is not a measurement of what it saved.
- **Prices are not hardcoded.** They live in `~/.claude/claude-token-prices.json`
  for you to edit, with the date you last verified them; after 90 days the
  reports say so. There is no pricing endpoint to fetch them from.
- **An unpriced model is reported, never guessed.** Its tokens are counted and
  its cost is left out, with a warning naming the model — rather than billing
  it silently at some other model's rate.
- **Compaction stats say what they are.** XML that only got minified is
  labeled as minified; a `.docx` reports the resulting token count instead of
  a percentage against zip bytes it was never comparable to.
- Costs are ESTIMATES from transcript usage and public per-MTok prices. Your
  bill and your plan window are the source of truth.

## Requirements

- Python 3 (preinstalled on Linux/macOS)
- Claude Code installed and logged in

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
level [0-2]            how much the harness injects (no arg: list the levels)
memory [id]            what was done in a session (default: the current one)
sessions [--days N]    list past sessions by id (default 14)
recall <query>         which past sessions mention something (default 60 days)
report [--days N]      tokens & cost for the period (default 30)
cache  [--days N]      prompt cache efficiency and cold-turn waste (default 7)
index  [path]          build the RAG index for a project (rerun after big changes)
search <query>         BM25 search over the indexed project
toon   [file]          compact JSON/XML/HTML/docx/logs before pasting (also stdin)
tips                   spot token-wasting config
```

Internal, called by Claude Code: `hook` (UserPromptSubmit), `posttool`
(PostToolUse), `statusline`.

Inside Claude Code: `/token <anything above>`, e.g. `/token report`.

## Files it owns (all under `~/.claude/`)

```
claude-token-state.json    ON periods + current level
claude-token-prices.json   the price table — yours to edit
claude-token-rag/          one BM25 index per project
claude-token-memory/       one note per compacted session (0600, holds your text)
claude-token-sessions.json which sessions already got the lean rules
commands/token.md          the /token slash command
settings.json.bak-claude-token   backup taken on first `on`
```

## Notes

- Claude Code hooks can only **add** context or **block** a prompt. They
  cannot switch models, ask you a question, or rewrite what you typed — which
  is why the model-choice rule asks Claude to ask you, rather than deciding.
- Everything injected for the model is written in English (tokenizes ~20%
  cheaper than Portuguese); everything printed for you is in Portuguese.
- The hook costs ~100 ms per prompt, of which ~20 ms is Python startup. It
  never blocks and never fails a prompt: on any error it stays silent.
