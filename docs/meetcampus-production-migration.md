# MeetCampus School Production Migration Plan

Status: simulation-runtime extension explicitly approved for school production on 2026-08-29

Approval reference: `codex-thread-2026-08-29-user-approved-complete-meetcampus-simulation-and-production-release`

## Source and target

- Source: reviewed Alembic revisions `20260828_meetcampus_world` and `20260829_meetcampus_runtime` in the UniKorn backend repository.
- Target: school production PostgreSQL database `prod_unikorn` used by `https://unikorn.hkust-gz.edu.cn`.
- Product data: deterministic private-beta world seed embedded in the revision; no runtime, test-user, forum, or shared-development data is copied.

## Change shape and exact expected counts

The original private-world migration creates 13 `meetcampus_*` tables. The runtime extension adds seven more tables and one field; neither migration updates, replaces, or deletes non-MeetCampus production rows.

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

The 2026-08-29 runtime migration is additive to that deployed base:

| Table or field | Change | Rows |
| --- | --- | ---: |
| `meetcampus_activity_definitions` | create and insert reviewed activity catalog | 21 |
| `meetcampus_observations` | create | 0 |
| `meetcampus_decisions` | create | 0 |
| `meetcampus_journeys` | create | 0 |
| `meetcampus_activity_sessions` | create | 0 |
| `meetcampus_activity_participants` | create | 0 |
| `meetcampus_resident_plans` | create | 0 |
| `meetcampus_scene_connections.path` | add non-null JSON route field | existing 22 rows receive `[]` |
| `meetcampus_worlds.seed_version` | update reviewed world version | 1 existing world row |

The 21 activity rows are the only newly inserted product records. Existing runtime
events, memories, relationships, stories, owner binding, appearance, commands, and
resident state are not replaced or deleted. Empty route geometry safely falls back
to scene anchors in the runtime, so the migration does not rewrite existing connections.

The reserved Mount resident is initially inactive and unowned. The first successful bootstrap by the single allowlisted, verified account `wtao565@connect.hkust-gz.edu.cn` binds it to that production user. The 19 other residents are explicitly synthetic and active.

## Existing production data impact

- No existing non-MeetCampus table or row is overwritten or deleted; no existing MeetCampus runtime row is deleted.
- Foreign keys reference the existing `users` table, but no user row is changed by the migration.
- Runtime binding and onboarding occur only after deployment, through authenticated application requests.
- The server-side beta allowlist contains only `wtao565@connect.hkust-gz.edu.cn`.
- The runtime migration updates only the MeetCampus world seed-version marker and adds
  an empty route JSON value to the existing 22 connection rows. It does not alter user,
  course, forum, scheduler, SISN, or CoursePlan data.

## Verification performed

- Backend full suite: 877 passed, 6 skipped; the final MeetCampus and production-deployment subset passes 70 tests with one environment-specific skip.
- Frontend full suite: 359 passed across 49 files.
- Frontend production build and bilingual key/hardcoded-copy checks pass.
- Local integrated rehearsal used a disposable SQLite database and validated: beta login, lazy owner binding, immersive onboarding, 19-to-20 resident activation, server snapshot, desktop view, 390 px mobile view, and no horizontal overflow.
- A disposable PostgreSQL 16 database successfully upgraded from an empty schema through the complete Alembic history to `20260829_meetcampus_runtime`; a repeated upgrade was a no-op, all seven runtime tables and the route field existed, and the activity catalog contained exactly 21 rows.
- Migration contract tests verify the new Alembic head, revision checksum, exact seeded-table counts, and rejection of any undeclared count drift.
- A production-snapshot rehearsal remains part of the trusted school activation workflow and must pass before the database can be promoted.
- Runtime contract tests cover three-minute non-teleporting journeys, independent
  invitation acceptance, deterministic competitive results, perspective switching,
  access denial, and the invariant that synthetic provenance is absent from cognition.

## Backup, activation, and rollback

1. The trusted school release controller creates a verified PostgreSQL backup before activation.
2. Alembic runs in the existing systemd migration oneshot before the `current` symlink changes.
3. Post-migration snapshot verification requires all existing table counts to remain unchanged and the five seeded table counts to match exactly.
4. Any migration, snapshot, service, or health-check failure blocks activation and preserves the previous application symlink.
5. Application rollback uses the previous immutable release. Database downgrade is never automatic; the verified pre-release backup is the recovery source if schema rollback is explicitly required.
6. CoursePlan service health is checked and must remain unchanged.

## Approved operator actions before activation

- This exact plan, including the original 75 rows, 21 new activity rows, seven new
  empty runtime tables, one connection field, and one world-version update, was
  explicitly approved under the reference above.
- Set the user-approved model credential as `MEETCAMPUS_AI_API_KEY` only in root-owned `/etc/unikorn/unikorn.env`; never commit it or print it in logs.
- Interactively install and verify the new root-owned `unikorn-background-worker.service` once.
- Validate both repositories in shared development, merge reviewed commits into each `main`, then release paired full SHAs through the `school-production` manifest.
- After activation verify the school homepage, `/api/healthz`, `/api/auth/oidc/status`, frontend `/health`, protected unauthenticated writes, the private MeetCampus worker status, and `courseplan.service`.
