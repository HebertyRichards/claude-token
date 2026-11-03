---
name: backend
description: Builds and reviews server-side code in the project's own architecture - routes, services, repositories, database queries and migrations, caching, external API calls and error handling - with no N+1 queries, safe migrations, timeouts on every outside call and logs that never leak data. Use when writing or changing an endpoint, service, query, migration, background job or integration, when the API is slow or erroring, or when asked to review backend code without changing it.
---

# Backend

The project already chose its layers, its database and its error format. Extend them; do not bring new ones.

## 1. The project's rules come first

Read `AGENTS.md` / `CLAUDE.md` (layers, commands, conventions), `CONTEXT.md` (business rules) and `docs/adr/`. They win over this skill. Find the existing pattern before writing: a similar route, its service, its repository, its schema, its tests. The project's `.claude/settings.json` deny rules and guard hooks stay in force; migrations and database resets are often blocked on purpose.

**Find the code with `claude-token search <words from the task>` before any grep or Glob**: it returns the `file:start-end` ranges to read, and builds the project's index on first use. `[claude-token] RAG` pointers already in the prompt come from the same search: read those first. Outside git, run `claude-token index <project folder>` once; without `claude-token`, use `grep -rn`.

## 2. Pick the mode

| The user said | Mode | What you may touch |
|---|---|---|
| build, add, fix, implementa | **Build** | the files the change needs, in the project's layers |
| review, analyze, analisa, "não modifique" | **Review only** | nothing: report findings |

## 3. Rules every change meets

| Area | Do | Avoid |
|---|---|---|
| Layers | route → service → repository (or the project's own split); validation at the edge with the project's schema library | SQL in the route, HTTP objects in the repository |
| Access | who is logged in comes from the session/token dependency; queries by id filter by owner and tenant | trusting an id or role from the body |
| Writes | allow-listed fields; server computes totals, status, owner | saving the whole body, ORM mass update |
| Queries | `JOIN` or `WHERE id IN (...)`, eager loading; indexes on WHERE, JOIN and ORDER BY columns; `EXPLAIN` before choosing between subquery and JOIN | a query inside a loop (N+1), `SELECT *` sent to the client |
| Transactions | one transaction per use case; unique constraints as the real guard against races, with the conflict turned into a 409 | check-then-insert with no constraint |
| Migrations | reversible, small, data and schema apart; the project's own command | editing an applied migration, dropping data without a backup step |
| Cache | the project's key namespace and TTL; invalidate on every write that changes it | caching per-user data under a shared key |
| Outside calls | timeout, at most 2 retries with growing waits, a fallback; catch the client's network errors and map them to the project's error codes | an unhandled timeout that becomes a 500 |
| Errors | the project's error codes and handler; generic message out, detail to the log | stack traces, SQL or versions in a response |
| Logs | structured, with request id and user id; no passwords, tokens, cookies, auth headers, reset links or whole bodies | `print` / `console.log` left in, logging personal data |
| Responses | only the fields the client needs, through the schema/serializer | returning the ORM object as is |

## 4. Verify

- Run the project's checks from `AGENTS.md` (tests, lint, types), quietly: `pytest -q --tb=short`, `vitest run --reporter=dot`, `phpunit --no-progress`.
- A new endpoint gets tests for: success, validation error, not logged in, someone else's record (404), and the race or conflict case if it writes.
- A new query on a listing, report or big table: show its `EXPLAIN` if the database is reachable; otherwise say it was not checked.

## 5. Report

Review only, most severe first:

```
[high|medium|low] file:line: what fails, when, and for whom
Evidence: Proven (read the path / ran it) | Inferred (what is unconfirmed)
Fix: one line
```

Build mode: files changed, tests and their result lines, what was not verified.

## Spend few tokens

Find the pattern with `grep -n` and read by range. Quiet test runners; long output to a file, then the tail. Never dump query results or API bodies whole: `| jq`, `| head`, or `| claude-token toon`, and never if they may hold personal data.
