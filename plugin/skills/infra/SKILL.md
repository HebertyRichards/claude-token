---
name: infra
description: Writes and reviews the infrastructure an app runs on - Docker and Compose, Kubernetes manifests, databases (PostgreSQL, MySQL/MariaDB) and Redis as services, cloud resources (AWS, GCP, Azure) and Terraform, servers and shared hosting, backups, monitoring and logs - private by default, least privilege, no secret in a file, and never a write to a live environment run by the agent. Use when creating or changing a Dockerfile, compose file, Kubernetes manifest, Terraform, cloud resource, database or Redis setup, backup or monitoring, when something fails only in a server or container, or when asked to review infrastructure without changing it.
---

# Infra

Infrastructure is where a small slip costs the most: a public port, a deleted volume, a key in a file. Write it so the safe choice is the default, and leave every command that changes a live environment for the user to run.

## 1. The project's rules come first

Read before writing. These win over this skill:

- `AGENTS.md` / `CLAUDE.md`, `docs/adr/`: where it runs, how it deploys, choices already made (the cloud, the database, the host). Do not swap a host or provider silently.
- What exists: `Dockerfile`, `compose*.yml`, `*.tf`, `.github/workflows/`, `nginx.conf`, `.htaccess`, `Procfile`, `vercel.json`, the cloud templates. Change the smallest part.
- `.env.example` for the variable names. Never open `.env` or print a secret; refer to variables by name.
- `.claude/settings.json`: denied commands and guard hooks. Never work around them.

**Find the code with `claude-token search <words from the task>` before any grep or Glob**: it returns the `file:start-end` ranges to read, and builds the project's index on first use. `[claude-token] RAG` pointers already in the prompt come from the same search: read those first. Outside git, run `claude-token index <project folder>` once; without `claude-token`, use `grep -rn`.

Queries, migration content and cache keys in the code belong to backend work, and the deploy pipeline and releases to the cd work; this skill covers where they all run.

## 2. Pick the mode

| The user said | Mode | What you may touch |
|---|---|---|
| create, configure, ajusta, sobe local | **Build** | config files and local containers |
| review, audit, analisa, "não modifique" | **Audit only** | nothing: report findings |
| apply, migrate in prod, restore, scale | **Operate** | nothing in the environment: read-only checks, then commands for the user |

Operate: run only what reads (`terraform plan`, `docker compose config`, `kubectl get`, `aws ... describe`, `gcloud ... describe`). Anything that writes to a shared or live environment (`apply`, `destroy`, `DROP`, `FLUSHALL`, `docker volume rm`, a restore, a scale) you write out for the user to run: the exact command, what it changes, and how to undo it. No undo? Say so first. Local containers on this machine are yours to start and stop, never to delete volumes of.

## 3. Rules every setup meets

| Area | Do | Avoid |
|---|---|---|
| Exposure | databases, Redis and queues on a private network, reachable only by the app | 5432, 3306, 6379 or 27017 open to the internet; SSH from `0.0.0.0/0` |
| Secrets | env vars from the host, the CI secret store or a secret manager; `.env` in `.gitignore`, `.env.example` with names only | a secret in a Dockerfile `ENV`/`ARG`, a compose file, a `.tf`, a workflow or an image layer |
| Access | one user or role per app with only what it uses; database user without superuser; cloud policy with named actions and resources | `*` actions, root keys, the app connecting as the database owner |
| Images | pinned version tags, multi-stage build, a non-root `USER`, `.dockerignore` with `.git`, `node_modules`, `.env` | `:latest`, build tools in the final image, `COPY . .` before installing dependencies |
| Compose | `healthcheck` on every service, `depends_on: condition: service_healthy`, named volumes, only public ports published | the database port published "for testing" and forgotten |
| Data | automatic backups, kept off the server, and a restore tested at least once; `prevent_destroy` or deletion protection on databases and buckets | a backup nobody ever restored; a volume deleted to "start clean" |
| Redis | a password or ACL, `maxmemory` with an eviction policy that fits (cache: `allkeys-lru`; queue or session: `noeviction`), a TTL on cache keys, persistence only if the data cannot be rebuilt | Redis as the only copy of data that matters; `KEYS *` in production |
| Database server | TLS outside a private network, a pool sized below `max_connections`, slow query log on, migrations that keep the old code working (add, backfill, then remove) | a migration that drops or renames a column the running version still reads |
| Cloud storage | block public access at account and bucket level, encryption on, signed URLs for private files | a public bucket to "share a file" |
| Terraform | remote state with locking and encryption, provider versions pinned (`~> 5.0`), `plan` reviewed before `apply`, tags for cost | local state, `apply` without a plan, secrets in variables without `sensitive = true` |
| Kubernetes | requests and limits on CPU and memory, readiness and liveness probes, `runAsNonRoot` and a read-only root filesystem, secrets from a Secret or an external store (never a ConfigMap), pinned image by digest, a namespace per app | pods without limits, `privileged: true`, `hostPath` mounts, a `LoadBalancer` for an internal service |
| Monitoring | a `/health` endpoint, an outside uptime check that alerts someone, an error tracker (Sentry or similar) with personal data scrubbed, structured logs without passwords, tokens or personal data, log size limits, alerts on what users feel (errors, latency, down) | finding out from a user that it is down; alerts on every CPU spike; logs that grow until the disk fills |
| Cost | the monthly estimate when adding a paid resource, and a budget alert on the account | an always-on instance for a weekly job |

Shared hosting (Hostinger and similar): no Docker or root. The app folder sits outside `public_html` except its entry point, settings come from the panel's environment or a file above the web root, cron from the panel, and the database user only reaches its own database.

## 4. Verify

- Config: `docker compose config -q`, `hadolint Dockerfile`, `terraform fmt -check && terraform validate`, `kubeconform` or `kubectl apply --dry-run=client`, `nginx -t`, the ones installed.
- Local: build and start, wait for the health checks, hit `/health`, read only the last log lines (`docker compose logs --tail=50 <service>`).
- Cloud: `terraform plan` and read the summary line (add, change, destroy). Any destroy you did not expect stops the work.
- Security scan if installed: `trivy config .`, `checkov -d .`. Report findings; do not mute them.
- Say plainly what was not run (a restore, a real cloud apply) and leave those commands to the user.

## 5. Report

Build mode: files changed, what each service exposes, the checks run with their result lines, and the cost of anything paid.

Audit only: one line per finding, worst first: `[severity] file:line: what is exposed or fragile → fix`. Severity: critical (data or keys reachable from outside), high (data loss on one mistake), medium, low.

Operate mode: the read-only checks with their result lines, then each command for the user with what it changes and its undo.

## Spend few tokens

- Never paste whole `plan` output, logs or `describe` JSON: the summary line, `--tail`, `| jq '<field>'` or `| claude-token toon`.
- Read one existing file as the pattern; do not dump every manifest.
- Prefer the project's own scripts (`make`, `pnpm run`, `composer`) over new ones.

## Never

- Print, paste or commit a secret, a connection string with a password, or a state file.
- Run a write command against a live or shared environment yourself, even when asked: write it for the user.
- Delete a volume, a database, a bucket or a backup to fix a setup problem.
- Open a database, Redis or admin port to the internet, even "for a minute".
