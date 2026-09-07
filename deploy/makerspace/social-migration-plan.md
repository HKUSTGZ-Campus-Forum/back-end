# MakerSpace cover and reaction migration

Status: implementation and disposable PostgreSQL rehearsal complete; school production NOT approved or activated for this change.

## Exact scope

- Source: repository Alembic schema only. No dev users, works, uploads or reactions are imported.
- Target: school `prod_unikorn` at `unikorn.hkust-gz.edu.cn`.
- Production revision: `20260907_teamup_makerspace` -> `20260907_maker_social`.
- Add nullable `maker_spaces.cover_file_id` (integer FK to `files.id`) plus its index. No backfill, no UPDATE, no overwritten or deleted work records. Existing values are null.
- Create `maker_likes` and `maker_favorites`, each with `(space_id,user_id)` primary key, foreign keys to spaces/users, creation time and a user index. Both start with exactly 0 rows.
- Inserts/updates/deletes of existing product records: exactly 0. TeamUp owner, external path, reviewed metadata and runtime remain unchanged. No user, TeamUp business, course, scheduler or forum data changes.
- No dependencies, Nginx, systemd, worker quota or external application changes.

The last verified school release had one MakerSpace (TeamUp); a fresh public catalog GET on 2026-09-07 still returns published TeamUp with creator ID 1256, but it does not reveal private-space counts. A fresh read-only school preflight on 2026-09-07 could not complete because SSH to `unikorn-school` timed out; do not present that earlier count as a fresh database observation. Before activation, read current Alembic head, space count, reserved slug collisions (`users`, `favorites`) and absence of the new tables. If the schema/head differs, stop and reconcile rather than blindly applying this plan.

## Rehearsal and safeguards

Disposable PostgreSQL upgraded from the school's previous revision through the current TeamUp schema and then this social migration twice. The complete existing TeamUp row was identical apart from the new null field; user/post/course counts stayed identical; the two new tables had zero rows and valid composite keys/foreign keys. A separate pristine main database reached `20260907_merge_maker_social` twice. No Agent tables were added by the school-path rehearsal.

Main contains an additional no-op `20260907_merge_maker_social` joining its independent development lineage. A school candidate must branch from the current school source and contain only `20260907_maker_social`, with its matching SHA256SUMS and expected-head checks. Do not deploy main's unrelated Agent lineage to school.

The installed trusted school controller must create and verify a fresh database backup before Alembic and atomic paired application activation. Save its backup path, SHA256 and activation receipt in the release evidence. If migration or health checks fail, retain the current application release; never bypass controller checks.

Application rollback uses the prior paired source while retaining the additive column/tables and any new user reactions/uploads. No automatic schema downgrade or data deletion is allowed. Full database restoration or dropping these tables requires a separate explicit plan/approval because it could discard subsequent user activity.

## Approval

Pending a new user approval of this exact additive production schema plan. Do not set `database_change.approved=true`, invent an approval reference or reuse the initial MakerSpace approval. After approval and successful fresh preflight, prepare exact main-ancestor frontend/backend school candidates, run paired validation, and change only the school-production manifest. Recheck the required school health/auth/write endpoints plus unchanged TeamUp, MeetCampus and CoursePlan services after activation.
