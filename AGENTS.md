# UniKorn backend agent instructions

This file contains backend-specific operational boundaries. Read
`docs/production-environment.md`, `deploy/school/README.md`, and `CAMPUS_SSO.md`
before changing authentication, deployment, migrations, Nginx, SISN ingest, or
production data.

## Environments

- Shared development is `https://dev.unikorn.axfff.com`; backend `main` still
  deploys there through GitHub Actions.
- Active production is `https://unikorn.hkust-gz.edu.cn` on the school host
  reached locally as `unikorn-school` (`wtao@10.121.15.221`).
- `https://unikorn.axfff.com`, `/data/prod_unikorn/*`, the `production` branch,
  and GitHub workflows targeting them belong to the former axfff production
  stack. They are not the active school-production release path.
- The school host also runs CoursePlan at
  `https://scheduler.unikorn.hkust-gz.edu.cn`; never modify or interrupt
  `/srv/course-scheduler` or `courseplan.service` as a side effect of UniKorn work.

## School production releases

- Release only clean committed frontend and backend checkouts identified by full
  40-character SHAs. Never publish a dirty local worktree.
- Routine production releases are controlled by the backend repository's
  `school-production` branch. That branch may change only
  `deploy/school/school-production-release.json`, which pairs one backend `main`
  SHA with one frontend `main` SHA. Never merge or rebase `main` into the control
  branch and never treat a push to either repository's `main` as production approval.
- Update the manifest with `tools/update_school_production_release.py`, commit it
  on `school-production`, and push. GitHub validates both candidates; the
  root-owned school-host controller deploys only a successful validation and
  only forward-moving SHAs. Do not manually manufacture the validation status.
- Agents must not set `database_change.approved=true` or supply an approval
  reference unless the user has explicitly approved the required production
  migration/data plan in the current task. Changes below `migrations/` or
  `app/data/` are blocked otherwise.
- The installed controller is the only exception to the interactive-sudo rule:
  its root-owned systemd oneshot may invoke the trusted installed copy of
  `deploy-release.sh`. Installing/updating the controller, manual fallback
  releases, Nginx changes, restores and rollback remain interactive-sudo actions.
- `deploy-release.sh --activate` owns the verified backup, Alembic, immutable
  release, `current`/`previous` switch, service restart and health gates; do not
  bypass those protections.
- Run `deploy/school/activate-nginx.sh` only when reviewed Nginx templates changed
  or a specifically approved migration gate must be removed. It creates a
  rollback snapshot before reloading.
- A green release requires public checks for `/`, `/health`, `/api/healthz`,
  `/api/auth/oidc/status`, and every newly changed proxy/write route, plus a
  check that `courseplan.service` remains active. `systemctl active` alone is
  not sufficient evidence.
- Except for the installed production-controller oneshot described above,
  host-changing scripts and formal `verify-local.sh` checks require interactive
  `sudo`. Never put a sudo password in arguments, files, logs, CI, or chat.

## Database and product data

- Before any production schema, seed, course, offering, curriculum, repair,
  replacement, deletion, or backfill, give the user a migration plan covering
  source, target, tables, operation type, estimated rows, overwrite behavior,
  dry-run, backup, and rollback; wait for explicit approval.
- Runtime/test users, posts, comments and debugging records do not move from dev
  to production. Only required schema and reviewed product data move.
- Prefer idempotent, repeatable, dry-runnable migrations. Production schema is
  forward-only after activation unless writes are stopped and a verified backup
  is deliberately restored with the matching application release.
- Never allow the school database and former axfff production database to accept
  writes concurrently.

## Authentication and SISN

- HKUST(GZ) OIDC SSO is the only end-user login method. Do not restore password
  login, registration, recovery, reset, or password-change routes.
- Keep SSO secrets server-side. Production must use
  `FRONTEND_BASE_URL=https://unikorn.hkust-gz.edu.cn` and the exact callback in
  `CAMPUS_SSO.md`.
- The official SISN production feed is local to the school host: CoursePlan
  fetches the allowlisted source and signs a loopback push to UniKorn. Public and
  SSR Nginx listeners must return `404` for the ingest path.
- Enabling SISN receiver/push/timer and applying offering changes are separate
  privileged operations. Review the signed dry-run and create a verified DB
  backup before the first apply; do not infer activation merely because code is
  present on `main`.

## Server and secret boundaries

- UniKorn runs under `/srv/unikorn` using `unikorn-backend.service`,
  `unikorn-frontend.service`, `unikorn-redis.service`, local PostgreSQL database
  `prod_unikorn`, and loopback ports documented in
  `docs/production-environment.md`.
- `/etc/unikorn/unikorn.env` is root-managed, mode `0640`, and parsed by systemd;
  never shell-source it or copy secret values into repository files.
- UniKorn, CampusA2A, and PlaceHelper are different projects. Do not borrow
  unrelated hosts, SSH aliases, or credentials for UniKorn production work.
- For school IP-limited APIs, confirm the approved egress environment instead of
  guessing from the phrase “IP limited”.

## Verification

Run the backend test suite and migration tests appropriate to the change. School
deployment scripts are covered by `tests/test_school_production_deployment.py`;
update that contract when changing `deploy/school/*`.
