# UniKorn school production deployment

This directory is the current production deployment runbook for
`https://unikorn.hkust-gz.edu.cn` on `10.121.15.221`. It never proxies the school
domain to the former axfff host and does not modify `/srv/course-scheduler` or
`courseplan.service`. The migration sections below remain for recovery and audit;
routine releases normally use the exact-commit deployment and verification steps.
The stable environment map and legacy-host boundary are summarized in
[`docs/production-environment.md`](../../docs/production-environment.md).

## Fixed topology

- Nuxt: `unikorn`, `127.0.0.1:3000`
- Flask/Gunicorn: `unikorn`, `127.0.0.1:8001`
- SSR API bridge: Nginx, `127.0.0.1:8081`
- PostgreSQL: `prod_unikorn`, local socket/peer auth as OS user `unikorn`
- Redis: dedicated `unikorn-redis.service`, loopback `127.0.0.1:6380`
- CoursePlan: unchanged at `127.0.0.1:3002`
- Releases: `/srv/unikorn/releases`, with `current` and `previous` symlinks
- Backups: `/srv/unikorn/backups/database`

The public `/api/` prefix is removed by Nginx before Flask. Browser requests use
same-origin relative URLs. SSR requests use the private `8081` bridge and the
same public path contract.

## Safety invariants

1. Run host-changing scripts only through interactive `sudo`; never put a sudo
   password in an argument, file, CI secret, or log.
2. `bootstrap-host.sh` verifies CoursePlan before and after its work. It creates
   a migration RSA private key on the school server; the private key must never
   leave `/etc/unikorn/migration-private.key`.
3. `/etc/unikorn/unikorn.env` is parsed by systemd, never shell-sourced. Keep it
   `root:unikorn` mode `0640`. Quotes, spaces, dollar signs, and backticks remain
   data rather than shell syntax.
4. Database exports and the old production environment are encrypted on the old
   host before GitHub sees them. The Artifact contains a CMS ciphertext only.
5. Rehearsal dumps are not final dumps. A final export stops the old API and its
   successful workflow deliberately leaves old writes frozen.
6. Restores go into a new database, verify SHA-256, `pg_restore --list`, all table
   counts, Alembic heads, extensions, and foreign-key validation, then rename the
   databases. The replaced school DB remains offline as a rollback database.
7. No database is ever dual-written.

## Ordered runbook

### 1. Bootstrap (requires interactive sudo)

Copy this committed checkout to the school host and run:

```bash
sudo deploy/school/bootstrap-host.sh
```

The command installs packages, creates the dedicated user/directories/database,
enables the isolated Redis instance, installs hardened units, and prints the
public migration certificate fingerprint. It does not activate UniKorn or alter
the live CoursePlan Nginx routing.

Copy only `/etc/unikorn/migration-public.crt` out of the server. Base64-encode it
without line wrapping and store it as GitHub production environment variable
`SCHOOL_MIGRATION_PUBLIC_CERT_B64`. The export workflow checks that it is valid
for at least another 14 days.

### 2. Configure production secrets

Create `/etc/unikorn/unikorn.env` from `unikorn.env.example` by a secure channel.
Preserve `SECRET_KEY` and `JWT_SECRET_KEY` from the encrypted former-production
environment through cutover so existing UniKorn sessions retain their intended
lifecycle. Copy OSS, mail, AI, and push credentials without printing them. The
restore gate compares every former-production value and permits differences
only for reviewed topology/proxy and rotated SSO settings. Keep
`CAMPUS_SSO_ENABLED=false` until the school rotates the previously exposed
client secret and supplies it safely.

### 3. Build and activate the exact commits

Both source directories must be clean, committed worktrees. Use full SHAs:

```bash
sudo deploy/school/deploy-release.sh \
  --backend-source /secure/staging/back-end \
  --frontend-source /secure/staging/front-end \
  --backend-sha FULL_BACKEND_SHA \
  --frontend-sha FULL_FRONTEND_SHA \
  --activate
```

The script builds immutable dependencies, applies Alembic through a systemd
oneshot unit, atomically changes `current`, restarts both services, verifies the
exact frontend version, and reverts the application symlink on failed health.
It takes a verified DB backup before replacing an existing release. Schema
downgrades are intentionally never automatic.

### 4. Split Nginx without touching CoursePlan

After direct health checks pass:

```bash
sudo deploy/school/activate-nginx.sh
sudo deploy/school/verify-local.sh
```

The activation has an automatic config rollback on `nginx -t`, reload, or local
health failure. It records the former Nginx files under
`/etc/unikorn/nginx-backups/<UTC timestamp>`.

### 4.1 Enable the official SISN production feed

The school production feed is deliberately local to this host: CoursePlan
fetches the IP-allowlisted official SISN snapshot, then signs and pushes it to
the loopback-only UniKorn backend. Public and SSR Nginx listeners return `404`
for the ingest path.

After activating the release and its Nginx configuration, enable the backend
receiver. This derives only the public half of CoursePlan's existing signing
key, atomically updates the UniKorn environment file without shell-sourcing it,
creates the protected snapshot archive, restarts the backend, and verifies that
unsigned requests fail closed:

```bash
sudo deploy/school/enable-sisn-production-ingest.sh
```

Install the CoursePlan production push units from its separately reviewed
checkout. The installer leaves the production timer disabled:

```bash
sudo deploy/install-school-sisn-production-push.sh
```

Run and inspect the signed dry-run before any offering mutation:

```bash
sudo systemctl start unikorn-sisn-push-production-dry-run.service
sudo journalctl -u unikorn-sisn-push-production-dry-run.service \
  --since '-10 minutes' --no-pager
```

After reviewing the source counts, candidate counts, omissions, warnings, and
import plan, create a verified database backup and perform the first apply:

```bash
sudo systemctl start unikorn-backup.service
sudo systemctl start unikorn-sisn-push-production.service
sudo systemctl enable --now unikorn-sisn-push-production.timer
sudo deploy/school/verify-local.sh --require-oidc
```

The timer runs every 30 minutes with jitter. Stop it before restoring the
pre-apply database backup. `sisn_sync_runs` and `/srv/unikorn/sisn-archive`
retain the audit trail and bounded source snapshots.

### 5. Encrypted rehearsal

Dispatch `export-production-migration.yml` with `export_kind=rehearsal`. Download
the ciphertext over an approved channel to the school server, record its
SHA-256, then run:

```bash
sudo deploy/school/restore-production.sh \
  --package /secure/path/unikorn-rehearsal-migration.cms \
  --mode rehearsal \
  --expected-backend-sha OLD_PRODUCTION_BACKEND_FULL_SHA \
  --expected-frontend-sha OLD_PRODUCTION_FRONTEND_FULL_SHA
```

This restores and verifies a candidate DB without promotion. Add `--promote`
only when the school stack is not receiving production traffic and the dry-run
data is needed for full API/SSO/OSS/mail acceptance. Never treat this rehearsal
copy as the final dataset while the old site remains writable.

### 6. SSO and external acceptance

After the rotated SSO secret is installed, change `CAMPUS_SSO_ENABLED=true`,
restart backend, and require:

```bash
sudo deploy/school/verify-local.sh --require-oidc
```

Then validate the real browser authorization-code/PKCE flow, callback, account
creation/binding, JWT refresh, local and provider logout, exact redirect URIs,
and the lack of open redirects. Also test OSS upload/read, mail delivery, admin,
both locales, mobile/desktop, posts/comments/search, course universe, academic
map, and planner. These checks need real authorized accounts and cannot be
replaced by synthetic production records.

Only after SSO acceptance, account binding/migration, session revocation, and
owner confirmation may CoursePlan's Better Auth namespace be disabled:

```bash
sudo deploy/school/disable-courseplan-legacy-auth.sh \
  SSO_ACCEPTED_ACCOUNTS_BOUND_SESSIONS_REVOKED
```

The rule blocks all `/api/auth/` endpoints at the public scheduler boundary,
not merely the two reported paths. Delete penetration-test accounts separately
with an audited owner-approved database operation; this repository deliberately
does not guess their identities.

### 7. Final maintenance window

1. Confirm school DNS, public DNS, gateway TLS, and end-to-end forwarding.
2. Dispatch the export workflow with `export_kind=final` and the exact freeze
   phrase. A successful workflow leaves the old API stopped.
3. Transfer the ciphertext and verify its out-of-band SHA-256.
4. Restore with `--mode final --writes-frozen` and both explicitly approved,
   full source-production SHAs; this always promotes only after
   all consistency checks pass.
5. Run the full acceptance matrix and watch logs/metrics.
6. Switch the school gateway/DNS. Do not reopen old writes.
7. After the observation gate, install `nginx/old-site-redirect.conf` on the old
   TLS endpoint, retaining its certificate directives. The `308` preserves the
   original path and query string.

## Rollback

- Application-only: `sudo deploy/school/rollback-release.sh`; it swaps `current`
  and `previous` after taking a verified DB backup.
- Database: stop both UniKorn services and rename the offline
  `prod_unikorn_rollback_<timestamp>` database back to `prod_unikorn`, or restore
  the verified custom-format backup. Never overwrite while the service writes.
- Cutover: stop school UniKorn first, restore school DB if required, route traffic
  back to the still-preserved old host, then explicitly reopen old writes. Never
  allow both environments to accept writes.
- Nginx: restore the timestamped files from `/etc/unikorn/nginx-backups` and run
  `nginx -t && systemctl reload nginx`.

Keep local backups at least 14 days. The school must additionally provide an
encrypted, monitored off-host backup destination and periodically perform a
full restore drill; same-disk backups do not protect against host loss.

## Network-administrator checklist

Send the following as one change request:

1. Publish internal and public DNS for `unikorn.hkust-gz.edu.cn` at the school
   ingress and forward it to `10.121.15.221:80`.
2. Terminate valid, automatically renewed TLS for that exact hostname on the
   upstream 443 gateway (or provide the certificate if TLS termination moves to
   the host). Forward only from the approved gateway.
3. Preserve `Host`, append `X-Forwarded-For`, and set `X-Forwarded-Proto=https`.
   The currently configured trusted ingress is `10.121.10.250`; notify the
   operator before adding or changing ingress IPs.
4. Keep `scheduler.unikorn.hkust-gz.edu.cn` forwarded to the same host; Nginx
   routes it only to CoursePlan `127.0.0.1:3002`.
5. Verify public DNS no longer returns NXDOMAIN and provide the complete public
   TLS certificate chain.
6. Rotate the `unikorn.client` SSO secret and deliver it by a secure channel for
   `/etc/unikorn/unikorn.env`; never email or paste it into a ticket/chat.
7. Provide monitored off-host PostgreSQL backup storage and a restore-test plan.
8. Confirm continued use of Alibaba OSS, mail/SMTP, and third-party APIs complies
   with school policy and that gateway egress permits them.
