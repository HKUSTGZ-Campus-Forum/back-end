# UniKorn school production environment

This is the current environment and operations reference for UniKorn. The
executable safety contract remains `deploy/school/README.md` and the scripts in
that directory.

## Environment map

| Environment | URL | Purpose | Release path |
|---|---|---|---|
| Local frontend | `http://localhost:3000` | UI/API development | local Nuxt and usually local Flask `:8000` |
| Shared development | `https://dev.unikorn.axfff.com` | Integration testing | frontend and backend `main` GitHub Actions |
| Active production | `https://unikorn.hkust-gz.edu.cn` | User-facing UniKorn | exact-SHA joint release on the school host |
| Independent CoursePlan | `https://scheduler.unikorn.hkust-gz.edu.cn` | School scheduler and official SISN fetcher | separate service on the same host |
| Former axfff production | `https://unikorn.axfff.com` | Preserved migration-era stack | not the current production target |

The repository still contains `production`-branch workflows and operations for
the former axfff host. Their `production` label does not mean school production.
Do not run them for a normal release to `unikorn.hkust-gz.edu.cn`.

## School host

- SSH target used by current operators: `unikorn-school` →
  `wtao@10.121.15.221:22`
- Hostname: `tpds-planner-app-ub2403-prod-01`
- Trusted ingress: school gateway `10.121.10.250`, which terminates TLS and
  forwards to host Nginx `:80` while preserving Host and forwarding scheme/IP
- UniKorn root: `/srv/unikorn`
- Immutable releases: `/srv/unikorn/releases/`
- Active and rollback links: `/srv/unikorn/current`, `/srv/unikorn/previous`
- Verified DB backups: `/srv/unikorn/backups/database/`
- Nginx snapshots: `/etc/unikorn/nginx-backups/`
- Runtime environment: `/etc/unikorn/unikorn.env`, owner `root:unikorn`, mode
  `0640`
- Independent CoursePlan root: `/srv/course-scheduler`

Never store passwords, private keys, OIDC secrets, signing keys, database URLs
with credentials, or environment-file contents in GitHub, logs, commands, or
chat.

## Fixed topology

| Component | Unit / identity | Listener or data |
|---|---|---|
| Nuxt | `unikorn-frontend.service` / `unikorn` | `127.0.0.1:3000` |
| Flask/Gunicorn | `unikorn-backend.service` / `unikorn` | `127.0.0.1:8001` |
| SSR API bridge | Nginx | `127.0.0.1:8081` |
| Redis | `unikorn-redis.service` | `127.0.0.1:6380/0` |
| PostgreSQL | peer auth as OS user `unikorn` | database `prod_unikorn` |
| Backup scheduler | `unikorn-backup.service` / `unikorn-backup.timer` | `/srv/unikorn/backups/database/` |
| CoursePlan | `courseplan.service` | `127.0.0.1:3002` |
| Public proxy | `nginx.service` | `/api/` → `8001`; other UniKorn paths → `3000` |

All application and data ports must remain loopback-only.

The verified 2026-08-22 runtime baseline was Ubuntu 24.04, Python 3.12, pinned
Node v20.19.6 at `/opt/node-v20.19.6-linux-x64/bin/node`, PostgreSQL 16, Redis 7,
and Nginx 1.24. Patch versions can change; query the host before version-sensitive
maintenance. `deploy-release.sh` intentionally refuses a missing pinned Node
runtime.

## Authentication contract

- HKUST(GZ) OIDC SSO is the only end-user login method.
- Production must set
  `FRONTEND_BASE_URL=https://unikorn.hkust-gz.edu.cn`.
- Registered callback:
  `https://unikorn.hkust-gz.edu.cn/api/auth/oidc/callback`.
- `/api/auth/oidc/status` must report `enabled: true`,
  `flow: authorization_code_pkce`, and `provider: HKUST(GZ)`.
- Password login, registration, recovery, reset, and password changes remain
  disabled. See `CAMPUS_SSO.md` for identity linking and onboarding rules.

## Exact-commit release

1. Verify frontend and backend locally and through the shared dev environment.
2. Merge reviewed changes to both repositories' `main` branches.
3. Fetch `origin` and record both full 40-character `origin/main` SHAs.
4. Prepare clean committed checkouts matching those SHAs on the school host.
5. Run through interactive sudo:

```bash
sudo deploy/school/deploy-release.sh \
  --backend-source /absolute/staging/back-end \
  --frontend-source /absolute/staging/front-end \
  --backend-sha FULL_BACKEND_SHA \
  --frontend-sha FULL_FRONTEND_SHA \
  --activate
```

The controller verifies both source trees, builds one immutable release, creates
and verifies a database backup, runs Alembic through a systemd oneshot, switches
`current`, preserves `previous`, restarts both applications, and checks backend
readiness plus the exact frontend SHA. A failed migration does not change
`current`; do not bypass this gate.

Run `sudo deploy/school/activate-nginx.sh` only for reviewed Nginx template
changes, first activation, or removal of an explicitly approved migration gate.
It backs up the current config and restores it automatically on validation
failure.

## Verification

Formal host verification:

```bash
sudo deploy/school/verify-local.sh --require-oidc
```

Minimum public and read-only checks:

```bash
ssh unikorn-school 'readlink -f /srv/unikorn/current'
ssh unikorn-school 'systemctl is-active nginx unikorn-backend.service unikorn-frontend.service unikorn-redis.service courseplan.service'
curl -fsS https://unikorn.hkust-gz.edu.cn/health
curl -fsS https://unikorn.hkust-gz.edu.cn/api/healthz
curl -fsS https://unikorn.hkust-gz.edu.cn/api/auth/oidc/status
```

Exercise each changed write route through the public hostname. An unauthenticated
protected route should reach Flask and return JSON `401`; Nginx HTML `503`
usually indicates a write gate or proxy rule still blocks it. Finish an
authentication release with a real school-account SSO, refresh, and logout test.

## Official SISN production feed

CoursePlan fetches the school-IP-allowlisted SISN snapshot, signs it, and pushes
it to the loopback-only UniKorn receiver. Public and SSR Nginx listeners must
return `404` for the ingest path.

Code on `main` does not mean the feed is active. Enabling the receiver with
`enable-sisn-production-ingest.sh`, installing CoursePlan push units, reviewing
the signed dry-run, performing the first backed-up apply, and enabling the
30-minute timer are separate privileged steps. Follow section 4.1 of
`deploy/school/README.md`. Before database restore, stop the production timer.
Audit state lives in `sisn_sync_runs` and bounded snapshots under
`/srv/unikorn/sisn-archive`.

## Migration and rollback

- Production schema or product-data changes require an approved migration plan
  with row estimates, dry-run evidence, backup, and rollback.
- Runtime/test users, posts, comments and debugging data do not move from dev.
- Application rollback uses `sudo deploy/school/rollback-release.sh`, which
  backs up the database before swapping `current` and `previous`.
- Do not auto-downgrade schema. Stop writes and restore the reviewed backup or
  rollback database together with the matching application release.
- Never reopen the former axfff database for writes while school production is
  writable.
- Local verified backups retain 14 days by default. Same-disk backups do not
  replace encrypted monitored off-host backups and restore drills.

Do not hardcode a mutable current release SHA here. Inspect
`/srv/unikorn/current/release.json`, `/health`, and both repositories'
`origin/main` whenever exact versions matter.

Last reconciled: 2026-08-22.
