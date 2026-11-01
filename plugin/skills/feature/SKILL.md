---
name: feature
description: Plans and builds a new feature grounded in the project's real code and business rules, with no invented APIs, reuse before new code, small verified steps and lean token use. Use when adding a feature, endpoint, screen, integration or behaviour that spans existing code, or when asked to analyze how a feature should be built or what is missing in one, without changing the code.
---

# Feature

Code that calls only what provably exists, follows the project's own rules, and is done when its acceptance checks pass, not when it compiles.

## 1. The project's rules come first

Read before planning. These win over this skill:

- `AGENTS.md` / `CLAUDE.md`: commands, layout, conventions, definition of done.
- `CONTEXT.md`: business rules and the domain words. Use the project's words in names.
- `docs/adr/`: decisions already taken. Do not reopen one silently; if the feature needs to, ask.
- `.claude/settings.json`: denied commands and guard hooks. Never work around them.

A new business rule goes into `CONTEXT.md` in the same change, if the project has one.

## 2. Size it before you start

| Size | Looks like | Do |
|---|---|---|
| Small | 1-2 files, no schema, no auth | Read the code, make the change, run the check. Skip the plan. |
| Large | 3+ files, schema/migration, auth, money, external API | The full loop below. |

State the size in one line after reading the code, never from the request alone.

Analysis only (analyze, plan, "não modifique"): run steps 3-5 and stop. Report the map, the plan and the risks. No edits.

## 3. Discuss: only the gaps

Ask only what the code, `CONTEXT.md` and the request do not answer: the end state, hard constraints, what "done" means. One round, structured choices (AskUserQuestion) when the options are known. If the user says "just do it", proceed and mark each guess as UNKNOWN in the plan.

## 4. Map: what exists, verified

**Find the code with `claude-token search <words from the task>` before any grep or Glob**: it returns the `file:start-end` ranges to read, and builds the project's index on first use. `[claude-token] RAG` pointers already in the prompt come from the same search: read those first. Outside git, run `claude-token index <project folder>` once; without `claude-token`, use `grep -rn`.

```
[KNOWN]    app/routes/users.py:42 → get_current_user() → session, not request
[KNOWN]    app/models/user.py:10 → User(tenant_id, role)
[INFERRED] app/services/mail.py → probably sends via SMTP (not read yet)
[UNKNOWN]  rate limiting on this router → check before adding the endpoint
```

- KNOWN means you read it, at that line. A library call is KNOWN only if it is in the installed version (lockfile, `site-packages`, `node_modules/<pkg>/package.json` and its types).
- Never write code that depends on an UNKNOWN. Resolve it or ask.
- Reuse ladder, in order: code that already does it → the language or framework built-in → a dependency already installed → new code. A new dependency needs the user's yes.

## 5. Plan: small stories

```
1. Migration: add invite.expires_at       accept: alembic upgrade/downgrade clean     files: 1
2. POST /invites (owner + tenant filter)  accept: test 201, other tenant gets 404     files: 2
```

Each story touches at most 3 files and has one checkable acceptance line. Security-sensitive stories (login, permission, tenant, payment, upload, SQL with user data) carry the check that proves it, such as a second-tenant test.

## 6. Build and verify, one story at a time

- Change only what the story needs. No drive-by refactors, no placeholder `TODO` code.
- New logs carry ids, never passwords, tokens or personal data; no `print`/`console.log` left behind.
- After each story run its acceptance check plus the project's lint and type check, quietly.
- Done means every acceptance line passed and was shown, with the command and its result line.

## 7. Spend few tokens

- Map with `grep -n`, `git grep` and the `[claude-token] RAG` hits; read files by range, not whole.
- Do not re-read what you already read; keep the map as the memory.
- Quiet test runners; long output to a file, read the tail.
- Broad search across many files: one Explore subagent, and use only its conclusion.

## Report

The map (KNOWN/INFERRED/UNKNOWN), the stories with their status, the commands run with their result lines, and what is left or assumed.

Based on zero-hallucination-coder (mehanshbarthwal-lab, MIT), feature-forge (Jeffallan/claude-skills, MIT), karpathy-coder and minimalist (alirezarezvani/claude-skills, MIT). See THIRD_PARTY.md.
