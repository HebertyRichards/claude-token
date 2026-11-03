---
name: bugfix
description: Finds the proven cause of a bug from errors, stack traces and logs, then fixes it with a regression test that fails without the fix and passes with it, spending as few tokens as possible. Use when something is broken, throws, returns the wrong value, regressed, or a test is flaky, or when asked to analyze a project for bugs without changing it.
---

# Bugfix

Done means one test ran **red on the old code and green on the new**, and you saw both runs. You know the cause only when you can show it: a line of code, a database row, a log entry.

## 1. The project's rules come first

Before anything else, read what the project already decided. These win over this skill when they disagree:

- `AGENTS.md`, `CLAUDE.md`, `CONTEXT.md` (business rules), `docs/adr/` (decisions).
- The project's `.claude/settings.json`: denied commands and its own hooks. Never work around a deny or a guard hook; if it blocks the step you need, tell the user.
- The real test, lint and type-check commands written there. Do not invent others.

What looks like a bug may be a business rule. Check `CONTEXT.md` and `git log -S '<the exact line>'` before "fixing" it.

## 2. Pick the mode

| The user said | Mode | What you may touch |
|---|---|---|
| fix, corrige, resolve, conserta | **Fix** | the smallest set of files, plus one test |
| analyze, review, find bugs, analisa, "não modifique" | **Diagnose only** | nothing: no edits, no new test files, no commits |

In Diagnose only, you still run read-only commands (existing tests, linters, `git log`) and read code. You report; the user decides.

## 3. The loop

1. **Collect the evidence.** The full error message, the stack trace, the failing input. Read the frames that belong to the project, not the framework's. No stack trace? **Find the code with `claude-token search <words from the task>` before any grep or Glob**: it returns the `file:start-end` ranges to read, and builds the project's index on first use. `[claude-token] RAG` pointers already in the prompt come from the same search: read those first. Outside git, run `claude-token index <project folder>` once; without `claude-token`, use `grep -rn`.
2. **Label the cause.** For each candidate say which it is:
   - **Proven**: you saw it: a printed value, a run, a row in the database.
   - **Inferred**: it fits the evidence, but nobody has seen it yet.
   - **Unknown**: the evidence at hand cannot decide; say which file, log or record would.
   Check the likeliest first and discard each one by opening the code or running it; a story that sounds right is not a check. Regressions: `git bisect` against the last good tag.
3. **Red.** Write the smallest test that fails on the current code, named after the defect (`returns 500 when the token expired`), asserting what a caller sees, not a mock call count. A test that passes on the current code does not show the bug: back to 2.
4. **Fix.** The smallest change that turns it green. A large fix usually means the test was too broad.
5. **Red again on purpose.** Revert only the fix, run the same test, see it fail, restore the fix. If it stayed green the test is decoration: rewrite it.
6. **Everything.** The project's full suite and type check, quiet. A test that pinned the old behaviour is a finding: read its history before changing it.

Cannot reach the code from a test (a closure in a huge factory, a route handler)? Extract the logic and test it, or write a structural test that scans the source for the invariant, and say which kind it is in the test's header.

## 4. Spend few tokens

- Quiet runners: `pytest -q --tb=short -x`, `vitest run --reporter=dot`, `jest --silent`, `phpunit --no-progress`. Run the one failing test first, the suite once at the end.
- Logs and long output go to a file; read `tail -50` or `grep -n` the error, never the whole thing.
- Read files by range around the stack frame (`offset`/`limit`), not whole. If the prompt came with `[claude-token] RAG` hits, start there.
- JSON, HTML or XML from an API: `| jq`, `| grep`, or `| claude-token toon`.
- Do not re-read a file you already read unless it changed.

## 5. Report

One block per bug, most severe first:

```
[severity] file:line: what goes wrong, for whom
Cause: Proven | Inferred | Unknown: the evidence (log line, value, code path)
Red:    the command and the failure line it printed      (Fix mode)
Fix:    what changed, in one line                        (Fix mode)
Revert: the failure line seen with the fix removed       (Fix mode)
Next:   what would prove it, if Inferred or Unknown
```

Say what you got wrong earlier in the investigation, if anything. Never write "fixed" or "reveals the bug" for something you did not see go red.

## Never

- Change several things at once, then keep whichever made it pass.
- Widen or delete an assertion until the test passes.
- Leave `print`, `console.log`, `debugger` or temporary files behind.
- Edit anything in Diagnose only mode.
- Print or paste secrets, tokens or personal data from logs, `.env` or responses: quote the line number and the kind, masked.
