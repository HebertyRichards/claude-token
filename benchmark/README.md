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
    --prompt "The payment tests are failing. Fix the code without changing the tests." \
    --workspace ~/projects/my-api --kind bugfix \
    --check "pytest -q" --unchanged tests/test_payment.py \
    --model haiku --reps 3

# other models than the one your session runs on, older versions included
python benchmark/run.py ... --model opus --model sonnet --model haiku --model claude-opus-5

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
| `--model M` | anything `claude --model` takes — an alias (`opus`, `sonnet`, `haiku`) or a full id (`claude-opus-5`, `claude-sonnet-4-6`); repeat it to test several. It has nothing to do with the model your own session is on |
| `--subagent-model M` | the model for any subagent the task starts (default: the main thread's). `--model claude-opus-5-5 --subagent-model sonnet` measures an Opus session that delegates to Sonnet. The built-in `Explore` subagent is ruled out in that case: it keeps the main thread's model whatever is asked |
| `--reps N`, `--level 1\|2`, `--effort` | runs per arm, the ON arm's level, passed through |

- **Your project is never modified.** Every run works on a fresh copy in a temp
  folder. Version-control history, `node_modules`, virtualenvs and anything
  shaped like a credential are not copied (see below); if the task needs
  dependencies, install them with `--setup`.
- **The prompt must have 4 to 200 words**: outside that range claude-token's hook injects nothing
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

Your own `--setup` and `--check` commands obey the same fences: one that
involves git or a credential file is refused before anything runs, and they
execute in the same environment, where git is inert.

Afterwards every run is audited from its transcript, subagents included: the
shell commands that ran are counted, and if a forbidden one went *through* —
not refused, executed — the run is marked `invalid` and named in the report.

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
- **Each response at its own model's rate.** A price row covers exactly one
  version: Opus 5.5 is never billed as Opus 5, and a subagent on another model
  than the main thread is billed as that model. A version the table lacks is
  reported as unpriced, not guessed. The report shows the id an alias resolved
  to, the models subagents ran on and what they cost.
- **The model that ran is the model you asked for.** A run whose transcript
  shows another one is marked `invalid`.
- **Only runs that completed the task count.** A run that spent less because it
  stopped early is a failure, not a saving. Different success rates between the
  arms are flagged.
- **One folder, warm cache.** Every run works in the same temp folder, and one
  trivial call per model comes first. The folder's path is part of what the
  CLI sends, so a new folder per run meant some ten thousand tokens written
  to the prompt cache again each time; with a fixed one they are read, which
  on Opus is most of what a small run costs. The warm-up belongs to neither
  arm, and its cost is reported apart. `--no-warmup` skips it.
- **Against a target.** With `--kind baixo`, `medio` or `alto` the report sets
  the measured saving beside the band the tool aims for at that level (2–5%,
  7–12%, 15–23% of cost) and says whether it was reached.
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

The ceiling is enforced on each run, not noticed afterwards. From the headroom
left and what a dollar costs in plan points on your account, the benchmark
works out how much the next run may spend and hands that to the CLI as the
run's hard budget (`--max-budget-usd`): a run that would cross the line is cut
there and redone on `--resume`, and a run the headroom cannot pay for is not
started. What a run costs is taken from the task's own finished runs; a task
with none yet goes by the other results in the same folder — the median of the
runs on that model, of tasks of the same `--kind` when there are any — so a
dear task is not begun on the last few points only to be cut halfway. The
points-per-dollar rate is measured from your own runs and kept in
`results/plan-rate.json` for every later benchmark; until one has been
measured, a conservative default is used. If the usage cannot be read it runs
nothing; `--no-plan-guard` is for
accounts with no subscription, together with `--max-cost`.

## Results and resume

Everything a run produces goes to `benchmark/results/`, created on the first
run and covered by the repo's `.gitignore`, so nothing written there is ever
committed:
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

Two things keep a paid run from being wasted on a broken check:

- **Before spending anything**, the setup and check commands are run once on
  an untouched copy. A check that cannot even start — a wrong path — is an
  error there instead of a failure in every paid run, and one that already
  passes is reported as unable to tell done from not done. `--no-preflight`
  skips it.
- **Afterwards**, `--recheck [file]` verifies paid runs again without the
  model. Each run's final answer and the files it changed are kept with the
  results, so its working copy is rebuilt and the checks run again — with a
  corrected `--check` or `--expect` when the original was wrong.

Exit codes: `0` done, `1` bad configuration, `2` three errors in a row, `3`
stopped at a limit, `130` interrupted.

## What it does not tell you

- **Interactive cost.** A headless run has nobody to answer, so both arms get
  a line in the system prompt saying not to ask questions (your prompt itself
  is sent untouched). In real use
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
