# benchmark

Runs one task of yours twice — claude-token off, claude-token on — and reports
what each one spent. It is the controlled comparison the day-to-day reports
refuse to fake.

Nothing is canned: there are no bundled scenarios and no example project. You
describe the task and point at the project you actually work on, because a
number measured on someone else's toy code says little about yours.

Unlike the rest of this repo, **it calls the model**: every run is a real
headless `claude -p` session and spends real tokens.

```bash
python benchmark/run.py \
    --prompt "Os testes de pagamento estão falhando. Corrija sem alterar os testes." \
    --workspace ~/projetos/minha-api --kind bugfix \
    --check "pytest -q" --unchanged tests/test_pagamento.py \
    --model haiku --reps 3

python benchmark/run.py ... --dry-run      # the plan and your plan usage; runs nothing
python benchmark/run.py --resume           # continue the latest unfinished file
python benchmark/run.py --report           # print the latest comparison again
```

Requires `claude-token on` (the ON arm needs the hooks registered).

## Describing the task

| flag | |
|---|---|
| `--prompt "..."` or `--prompt-file F` | the request, exactly as you would type it into Claude Code |
| `--workspace DIR` | the project it runs in (default: the current folder) |
| `--kind`, `--name` | labels for the report: `bugfix`, `feature`, `analise`… |
| `--setup CMD` | run in the copy before each run: install dependencies, check out a state |
| `--check CMD` | run in the copy afterwards; exit 0 means the task was done. `{answer}` becomes a file holding the model's final answer |
| `--expect REGEX` | the final answer must match — for tasks that only answer |
| `--unchanged FILE` | a file the model must not touch (the tests, in a bugfix) |
| `--allow RULE` | an extra tool rule, in `--allowedTools` syntax: `"Bash(npm *)"` |
| `--model M` | anything `claude --model` takes; repeat it to compare models |
| `--reps N`, `--level 1\|2`, `--effort` | runs per arm, the ON arm's level, passed through |

- **Your project is never modified.** Every run works on a fresh copy in a temp
  folder. Version-control history, `node_modules`, virtualenvs and anything
  shaped like a credential are not copied (see below); if the task needs
  dependencies, install them with `--setup`.
- **The prompt must have 4 to 200 words** (counting a one-line note the
  benchmark appends): outside that range claude-token's hook injects nothing
  and the two arms would be identical.
- **Give it a way to verify.** Without `--check` or `--expect` the benchmark
  cannot tell a finished task from an abandoned one, and the report says so.
- **Tools.** The model may read and edit files inside the copy and run the
  commands you gave as `--check`. Anything else that would ask for permission
  is denied; add what the task needs with `--allow`.

The description is stored in the results file, so `--resume` takes no flags and
the file says by itself what was measured.

## What a run is kept away from

A run is unattended, so it is fenced in. These are guards, not a sandbox, and
they are layered so that one slipping does not open the door.

- **No repository.** No branch, commit, push or merge — not in the working
  copy, not in any other checkout on the machine:
  - the copy has no `.git`;
  - the prompt says so, in both arms;
  - `git` and `gh` tool calls are denied, and `--allow` cannot bring them back;
  - the run's environment makes git itself inert, which also covers a script
    that calls it (`npm version` commits and tags without one git tool call):
    it points at no repository and `git init` cannot create one, every
    transport is disabled (no clone, fetch or push), and `gh` gets a token
    that opens nothing.
- **No credentials.**
  - `.env*` files, key material (`*.pem`, `id_rsa`…), `.npmrc`/`.netrc` and
    secret-shaped data files (`secrets.json`, `credentials.yaml`…) are left
    out of the copy. The header lists what was withheld, so a false positive
    is visible; source files are kept (`password_reset.py` is code).
  - Reading or editing `.env*`, `~/.ssh`, `~/.aws`, the CLI's own credentials
    and the like is denied wherever they are — through the file tools and
    through shell commands such as `cat`.
- **No connectors.** MCP servers and claude.ai connectors are not loaded, so a
  run cannot open a pull request, post a message or touch a shared drive.
  Their tool definitions are therefore absent from both arms: absolute token
  counts run a little lower than in your real sessions.
- **Writes stay in the copy.** Edits are accepted only inside the working copy;
  a write anywhere else needs a permission nobody is there to give.

What the guards do not cover: the commands you allow run with your user's
rights. A task verified with `python …` lets the model run Python, and Python
can do whatever you can. Keep `--allow` narrow, and do not benchmark a task
whose instructions require touching what the guards protect.

Your `--setup` and `--check` commands are yours and run outside these fences.
The benchmark itself only ever reads from git: `git rev-parse` and
`git status` on its own repository, to stamp the results with the code version.

## What differs between the arms

One environment variable. Both arms run with your normal settings; the OFF arm
sets `CLAUDE_TOKEN_LEVEL=0`, so the hooks run and inject nothing — what
`claude-token level 0` does — and the ON arm sets it to the level under test.
`settings.json` is never edited, so a benchmark that dies cannot leave the
optimizer switched off, and your other sessions do not notice.

Every run then proves which side it was on: its transcript is searched for
hook output carrying the `[claude-token]` tag. An OFF run that has it, or an ON
run that does not, is marked `invalid` and left out of the comparison.

## How it is measured

- **From the transcript**, with the functions `claude-token report` uses:
  subagents included, deduped by message id, priced by your local table. The
  CLI's own `total_cost_usd` is stored beside it as a cross-check.
- **Only runs that completed the task count.** A run that spent less because it
  stopped early is a failure, not a saving. Different success rates between the
  arms are flagged.
- **The first arm alternates** from one pair to the next: the first run of a
  pair pays the cache write for the prefix both share, so a fixed order would
  favour the second.
- **Medians and ranges, not single numbers.** The verdict is on cost: `economizou`
  or `gastou mais` only when the two arms' ranges do not overlap; otherwise
  `inconclusivo`. With one run per arm it says `n=1` and nothing more — use
  `--reps 3` or higher before believing a difference.

## Limits

Before every run it reads your plan's windows from the endpoint `/usage` uses
and stops when a run could cross a ceiling:

| flag | default | |
|---|---|---|
| `--max-5h` | 80 | % of the 5-hour window (the plan has no daily one) |
| `--max-week` | 85 | % of the weekly window |
| `--max-cost` | none | estimated US$ for the whole benchmark |
| `--run-budget` | 3 | US$ for one run, via `--max-budget-usd` |
| `--timeout` | 900 | seconds for one run |

The margin is what the latest runs consumed (at least one point, the reading's
resolution): at 78% with runs taking 4 points each, it stops rather than land
on 82%. If the usage cannot be read it runs nothing; `--no-plan-guard` is for
accounts with no subscription, together with `--max-cost`.

## Results and resume

Everything a run produces goes to `benchmark/results/`, which is git-ignored:
`bench-<date>.json` (the description, every attempt, the comparison) and
`bench-<date>.transcripts/`. The per-copy folders the CLI creates under
`~/.claude/projects` are removed, so benchmark sessions do not show up in
`claude-token report` or `sessions`.

The JSON is rewritten atomically before each run starts and after it ends, so
it is always current. When the benchmark stops — a ceiling, the account's own
limit, Ctrl+C, a killed process — run `--resume`: settled runs are kept, the
rest are redone. A run that was in flight is recorded as `interrupted` with
whatever its transcript shows it spent, then run again. `--retry-errors` also
redoes runs that errored.

Exit codes: `0` done, `1` bad configuration, `2` three errors in a row, `3`
stopped at a limit, `130` interrupted.

## What it does not tell you

- **Interactive cost.** A headless run has nobody to answer, so every prompt,
  in both arms, ends with a line saying not to ask questions. In real use
  claude-token's scope rule makes the model ask which model to continue on;
  that extra round trip is not measured here.
- **Permission prompts.** Headless, a tool call that would ask for permission
  is denied instead, and the model spends a turn finding another way. An arm
  that tries such calls more often pays for it here and would not in a real
  session; the report prints how many were denied in each arm so you can
  discount it.
- **Other tasks.** One run measures one task. On a small one the injected rules
  can cost more than they save, and the benchmark will say so.
- **Plan percentage.** Cost is an estimate from public per-token prices; the
  plan window is the source of truth for what you have left.

## Tests

```bash
python -m unittest discover -s benchmark/tests
```

They drive the whole harness — description, ceilings, resume, recovery of a
killed run, arm validation — against a fake CLI in a temp home: no tokens, and
nothing under your real `~/.claude` is touched.
