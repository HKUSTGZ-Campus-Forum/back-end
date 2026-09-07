# MakerSpace school production plan

Status: **Approved and executed on 2026-09-07.** Approval reference: `codex:01a07a68-92bb-79f1-8396-f7e405107df7:2026-09-07:user-approved-makerspace-plan`.

The school controller activated backend `96faa515b6a4f266d123d9f3a1ba43af1e425076` and frontend `49bee6e792f043616745d307defd5887b82c3be2` under control commit `c1949dc5b8618e2e6269c895b202c0657bfae413`. Hosting and credentials are ready; this historical approval covers only the two migrations and installation below, never future changes. See [deployment record](school-release-20260907.md).

## Observed target and source

Read-only school audit on 2026-09-07:

- Host: `unikorn-school`, `tpds-planner-app-ub2403-prod-01`.
- Product: `https://unikorn.hkust-gz.edu.cn`; database `prod_unikorn`.
- Current backend: `5732d34c2b0dd0b6911b5d2123e535fffc70ee99`; frontend: `ff65d6bc4a1921be39abc3b43245dfd916185184`.
- Current Alembic revision: `20260903_recruitment_admin`.
- The specified creator email uniquely matches one active verified account (ID 1256). No MakerSpace tables exist.
- TeamUp is already healthy and independently deployed. Its runtime, current SHAs, database and signing controls are not migrated by this release.

Release preparation must produce a production backport from these school code bases, containing only the MakerSpace changes. The backport commits must enter their respective `main` ancestry before the paired school manifest may reference them. Main's existing Agent schema and the main-only merge revision are **not** part of this production data approval. Fill in the actual candidate SHAs after tests; do not substitute the current moving main heads.

## Exact product data changes

Run only these two new revisions, through the existing school release migration service:

1. `20260907_makerspace.py`: add `maker_spaces`, `maker_deployments`, `maker_audit_events`, `maker_runtime_sessions`, `maker_webhook_deliveries`, `maker_workers`, with indexes and references to existing users.
2. `20260907_teamup_makerspace.py`: add exactly one published `teamup` catalog row, owned by the verified account resolved from the user-specified email, and exactly one attribution audit row. Entry remains `/teamup/`.

Expected initial changes: **6 new tables, 1 catalog row, 1 audit row; 0 existing user rows updated, 0 existing TeamUp runtime rows changed, 0 forum/course/scheduler records changed, 0 deletions or replacements.** No dev users, posts, previews or test data are imported. Worker heartbeats and future sessions/builds are runtime records created after activation.

Recheck the creator match and table state immediately before activation using the read-only `tools/makerspace_preflight.py`. Abort on a missing/ambiguous account, conflicting catalog owner, unexpected partial schema or changed production base. The seed is repeat-safe; it must never silently transfer a conflicting existing registration.

## Infrastructure changes

- Install Docker plus checksum-pinned gVisor release `20260831.0`, its required companion files and digest-pinned Node/Python images. No creator build runs on the host.
- Add the root-owned worker under `/usr/local/libexec/unikorn-makerspace`, dedicated fetch user and review group, fixed-size volumes under `/srv/unikorn-makerspace`, and separate encrypted-credential configuration under `/etc/unikorn-makerspace`.
- Add worker, firewall and backup systemd units. Limits: 0.5 CPU/256 MB/64 processes per runtime, 768 MB and 300 seconds per build, 1 GB build disk, 256 MB persistent data per environment; at most 8 runtime instances initially. Preview and public storage are separate.
- Add only the MakerSpace bridge/firewall chains, denying host/private/lateral access and allowing public HTTP(S) plus configured public DNS. Inspect routes for `172.30.91.0/24` conflicts first.
- Include `nginx-locations.conf` in the school UniKorn server block: block public worker endpoints; proxy runtime requests with sandbox-compatible headers. Test Nginx before reload.
- Add the backend environment-file drop-in. Hosting remains disabled until the prepared-host sandbox check and normal paired release have succeeded.
- Do not restart or reconfigure TeamUp, MeetCampus or CoursePlan. Docker is a new service on this host; inspect firewall effects and all existing health checks before/after installation.

## Rehearsal and activation gates

Completed locally: PostgreSQL pristine migrations; rehearsal from the exact current school revision with a synthetic creator record (6 tables, 1 catalog, 1 audit, existing table counts unchanged); permission/review/bootstrap/logout tests; real gVisor build, health, hard disk limit, network denial, preview/public data isolation and verified filesystem backup; Chromium real HTTP gateway with third-party cookies blocked; Chinese/English desktop/mobile interface checks.

Activation gates (all completed for the recorded release): final source/CI gates, fixed candidate SHAs, this plan's explicit current approval, interactive host installation, school-host sandbox test and Nginx checks, and paired release activation. Local sandbox verification used a generated source fixture and does not establish a real private repository's GitHub authorization; each creator must complete the Deploy Key connection and a real build.

## Backup and rollback

The school release script must create and verify its standard `prod_unikorn` backup before Alembic and retain previous release links. Securely back up the dedicated backend encryption-key file and worker configuration under root-only storage; encrypted database fields cannot be recovered without the encryption key. Never export these files through the API or Git.

Creator data gets verified, filesystem-consistent compressed backups, retaining the last seven snapshots per volume. Stop the affected creator runtime before any data restore; verify the recorded digest, restore only its volume and recheck ownership/limits/health. Restoring app data requires a specific reviewed recovery plan, not a global database restore.

If activation or health checks fail, disable the MakerSpace worker and use the existing trusted school application rollback via interactive sudo. The new tables are additive, so the prior application can run while retaining them; automatic Alembic downgrade deliberately refuses to delete creators' data. Any full database restore requires a new plan covering writes since the backup. Revert only the new Nginx include/drop-in/units if needed, validate Nginx, and preserve MakerSpace volumes and secrets for recovery.

## Acceptance after activation

Verify school `/`, `/api/healthz`, `/api/auth/oidc/status`, `/health`; protected MakerSpace writes must reject unauthenticated callers. Verify catalog contains TeamUp with the intended author, old sidebar entry is replaced, private author/reviewer access and copied-link denial, and public worker paths return 404. Check TeamUp `/teamup/health` and `/api/teamup/healthz`, MeetCampus, and `courseplan.service`. Confirm release SHAs, Alembic revision, worker heartbeat, disabled failure behavior, new-space limits, review-to-public promotion and the backup timer. Do not declare success based solely on active systemd units.
