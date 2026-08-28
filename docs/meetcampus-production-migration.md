# MeetCampus School Production Migration Plan

Status: explicitly approved for school production on 2026-08-28

Approval reference: `codex-thread-2026-08-28-user-approved-meetcampus-migration`

## Source and target

- Source: reviewed Alembic revision `20260828_meetcampus_world` in the UniKorn backend repository.
- Target: school production PostgreSQL database `prod_unikorn` used by `https://unikorn.hkust-gz.edu.cn`.
- Product data: deterministic private-beta world seed embedded in the revision; no runtime, test-user, forum, or shared-development data is copied.

## Change shape and exact expected counts

The migration is additive. It creates 13 new `meetcampus_*` tables and does not update, replace, or delete rows in existing production tables.

Exact seeded rows accepted by the post-migration verifier:

| Table | Change | Rows |
| --- | --- | ---: |
| `meetcampus_worlds` | insert | 1 |
| `meetcampus_scenes` | insert | 12 |
| `meetcampus_scene_connections` | insert | 22 |
| `meetcampus_residents` | insert | 20 |
| `meetcampus_resident_states` | insert | 20 |

The remaining new tables must be empty immediately after migration:

- `meetcampus_owner_profiles`
- `meetcampus_commands`
- `meetcampus_events`
- `meetcampus_memories`
- `meetcampus_relationships`
- `meetcampus_stories`
- `meetcampus_bridges`
- `meetcampus_agent_runs`

The reserved Mount resident is initially inactive and unowned. The first successful bootstrap by the single allowlisted, verified account `wtao565@connect.hkust-gz.edu.cn` binds it to that production user. The 19 other residents are explicitly synthetic and active.

## Existing production data impact

- No existing tables or rows are overwritten or deleted.
- Foreign keys reference the existing `users` table, but no user row is changed by the migration.
- Runtime binding and onboarding occur only after deployment, through authenticated application requests.
- The server-side beta allowlist contains only `wtao565@connect.hkust-gz.edu.cn`.

## Verification performed

- Backend full suite: 848 passed, 6 skipped before the final route-matrix addition; MeetCampus and production-deployment suites pass after that addition.
- Frontend full suite: 293 passed.
- Frontend production build and bilingual key/hardcoded-copy checks pass.
- Local integrated rehearsal used a disposable SQLite database and validated: beta login, lazy owner binding, immersive onboarding, 19-to-20 resident activation, server snapshot, desktop view, 390 px mobile view, and no horizontal overflow.
- Migration contract tests verify the new Alembic head, revision checksum, exact seeded-table counts, and rejection of any undeclared count drift.
- A production-snapshot PostgreSQL rehearsal has not yet been executed; it is part of the activation workflow and must pass before the database can be promoted.

## Backup, activation, and rollback

1. The trusted school release controller creates a verified PostgreSQL backup before activation.
2. Alembic runs in the existing systemd migration oneshot before the `current` symlink changes.
3. Post-migration snapshot verification requires all existing table counts to remain unchanged and the five seeded table counts to match exactly.
4. Any migration, snapshot, service, or health-check failure blocks activation and preserves the previous application symlink.
5. Application rollback uses the previous immutable release. Database downgrade is never automatic; the verified pre-release backup is the recovery source if schema rollback is explicitly required.
6. CoursePlan service health is checked and must remain unchanged.

## Approved operator actions before activation

- This exact plan, including the 75 seeded product rows, was explicitly approved under the reference above.
- Set the user-approved model credential as `MEETCAMPUS_AI_API_KEY` only in root-owned `/etc/unikorn/unikorn.env`; never commit it or print it in logs.
- Interactively install and verify the new root-owned `unikorn-background-worker.service` once.
- Validate both repositories in shared development, merge reviewed commits into each `main`, then release paired full SHAs through the `school-production` manifest.
- After activation verify the school homepage, `/api/healthz`, `/api/auth/oidc/status`, frontend `/health`, protected unauthenticated writes, the private MeetCampus worker status, and `courseplan.service`.
