---
name: security
description: Reviews and hardens application security in any stack - access control (IDOR, cross-tenant), injection, auth and sessions, CSRF, secrets, uploads, redirects and SSRF, headers and CORS, and logs that leak - reporting each finding with its evidence and never exposing secret values. Use when code handles login, passwords, sessions, permissions, tenants, payments, uploads, user-supplied URLs or SQL with user data, or when asked for a security review or audit, with or without changing code.
---

# Security

A finding is a place in the code, the input that reaches it, and what an attacker gains. Without all three it is a hunch, and you say so.

## 1. The project's rules come first

Read `AGENTS.md`, `CLAUDE.md`, `CONTEXT.md` and `docs/adr/` first: who owns what, which roles exist, what is deliberately public. They decide what is a vulnerability here. The project's `.claude/settings.json` deny rules and guard hooks stay in force: never work around them.

## 2. Pick the mode

| The user said | Mode | What you may touch |
|---|---|---|
| fix, harden, corrige | **Fix** | the smallest change, plus a test that proves the hole is closed |
| review, audit, analyze, analisa, "não modifique" | **Review only** | nothing: report findings |

## 3. Never leak while looking

Security work handles exactly what must not spread:

- Never open, print or quote `.env`, key files, credentials or tokens. Need to know if a variable exists? Check its **name** in the code or `.env.example`.
- A secret found in code is reported by **file:line and kind** ("AWS key", "JWT secret"), never by value. Mask it: `sk_live_…4f2a`.
- Do not paste working exploit payloads into reports, commits or logs. Describe the input class ("an id from another tenant").
- Command output that may hold secrets or personal data goes to a file you read with `grep`, not to the conversation.
- Reports stay in the conversation, not committed to the repo, unless the user asks for a file.

## 4. What to check

Follow the data from the request to the database and back.

| Area | Look for |
|---|---|
| Access control | Every route checks who is logged in **from the session**, never from the request. Every query by id also filters by owner (`AND user_id = ?`) and by tenant (`AND tenant_id = ?`), including inserts, exports, reports and search. Someone else's record answers 404. |
| Mass assignment | Writes only fields from an allow-list. Price, total, discount, role, status and owner are set by the server, never by the body or a hidden field. |
| Injection | SQL only with placeholders or the ORM's bound parameters. No user input in raw SQL, shell commands, template strings, `eval`, file paths. |
| Output | HTML escaped by default; no `innerHTML` / `dangerouslySetInnerHTML` / `v-html` / `|safe` with user data. |
| Auth | Passwords with bcrypt/argon2 (`password_hash` in PHP). Generic login error. Rate limit on login, sign-up, reset and public forms. Reset tokens random, stored hashed, single use, short-lived; the link uses the configured domain, never the `Host` header. Changing password or e-mail asks for the current password and ends other sessions. |
| Sessions and tokens | Cookies `HttpOnly`, `Secure`, `SameSite`; session id regenerated at login; destroyed on the server at logout. JWT: algorithm pinned, expiry checked, refresh tokens rotated and revocable. OAuth: `state` bound to the browser that started the flow. |
| CSRF | Every state-changing form or cookie-authenticated endpoint has a token or a SameSite policy that covers it. |
| Fail closed | An error or timeout in an auth, permission, CSRF, token or payment check means **deny**. A fallback is only for non-critical data. |
| Redirects and URLs | Redirects only to internal paths (`/x`, not `//x`) or a fixed list. User URLs (webhooks, imports, previews): HTTPS, allow-listed host, public IP, no redirects followed. |
| Uploads | Type, size and extension checked; server-generated name; stored outside the public folder when possible. |
| Headers, CORS, cache | CSP with `frame-ancestors`, `nosniff`, HSTS. CORS from a fixed list, never `*` or an echoed origin with credentials. Logged-in responses `Cache-Control: private, no-store`. |
| Errors | The client gets a generic message; the detail goes to the log. No stack traces, SQL or versions in responses. |
| **Logs and prints** | No passwords, tokens, cookies, `Authorization` headers, reset links, full card numbers or whole request bodies in logs. No leftover `print`/`console.log`/`var_dump` of user or auth objects. Log ids, not personal data. |
| Dependencies | Lockfile committed; the ecosystem's audit (`pnpm audit --prod`, `composer audit`, `pip-audit`) run, not guessed. |

Use the project's own scanners if it has them. Run read-only commands only in Review mode.

## 5. Report

Most severe first, one block each:

```
[critical|high|medium|low] file:line: what an attacker can do, and who is exposed
Evidence: Proven (read the code path / ran a test) | Inferred (what is still unconfirmed)
Input:    the kind of input that reaches it (no working payload)
Fix:      one line; the test that would prove it closed
```

End with what you did not check. Fix mode: the test that failed before the fix and passes after, and the project's suite result.

## Spend few tokens

Locate code first with `claude-token search <words>`: it returns `file:start-end` ranges and builds the index on first use (outside git: run `claude-token index <project folder>` once; no `claude-token`: `grep -rn`). Then grep for the sinks (`execute(`, `raw(`, `innerHTML`, `redirect(`, `requests.get(`, `logger.`, `print(`) and read only around the hits. Do not read whole files you only need one route from.

Based on the dev-setup security rules and php-hostinger-security (dev-setup, MIT), and on security-pen-testing and senior-backend (alirezarezvani/claude-skills, MIT). See THIRD_PARTY.md.
