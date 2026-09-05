# Backend architecture

Source baseline: `d02ce0e`, reconciled 2026-09-05. Read [source map](source-map.md) to locate a subsystem and [testing](testing.md) before changing it.

## Request and runtime flow

Browser `/api/...` → public proxy → Flask blueprint without `/api` → route validation/permissions → service/model → PostgreSQL, Redis or external provider → serializer/JSON. The frontend's server-side API calls have a separate internal base and proxy path. [Local entry point](../run.py) additionally strips `/api` for the development server; [WSGI](../wsgi.py) and school Nginx define deployment behavior.

[create_app](../app/__init__.py) configures proxy trust and production-secret validation, initializes [extensions](../app/extensions.py), registers [blueprints](../app/routes/__init__.py), attaches Alembic and conditionally starts initialization/jobs. SQLAlchemy models are loaded through [model imports](../app/models/__init__.py). Do not import the app against an unreviewed environment for a read-only code inspection: startup helpers can mutate data.

The web process and [background worker](../app/background_worker.py) share the factory. Worker ownership is selected with configuration; the [school service units](../deploy/school/systemd) separate scheduled work. Migration/backup units explicitly disable startup initialization; the web unit reads its environment file and the committed school environment example still sets `AUTO_INIT_ON_STARTUP=true`. Do not assume web startup is read-only or change that setting without assessing the data initializers. Job registration lives in [sts_pool](../app/tasks/sts_pool.py), despite that historical filename covering more than STS.

## Domain boundaries

| Domain | Responsibility and guide |
|---|---|
| Identity | School OIDC authenticates people; JWT carries UniKorn sessions; downstream OAuth authorizes client applications. [Auth/API](features/auth-api.md) |
| Community | Posts/comments/tags/reactions, files, analytics/search, gugu and notifications. [Community](features/community.md) |
| Academic | Canonical courses/catalog rules; term offerings/sections/meetings; user academic state; cart/solver inputs; saved plans and popularity. [Academic](features/academic.md) |
| Administration | Permissions, audit logs, moderation, identity approval, feedback transitions, contests and carousel. [Administration](features/administration.md) |
| Model features | Assistant read-only context/chat and recruitment's bounded virtual challenge are distinct systems. [AI features](features/ai.md) |
| Shared services | Cache, OSS/STS, embeddings/matching and background maintenance. [Services](features/services.md) |

## Decisions to preserve

- Persistence is PostgreSQL with Alembic. Existing startup schema helpers support particular development paths; they do not replace the reviewed production migration lineage.
- Course rule authority is PCC catalog data, while SISN supplies offerings/quota/meetings. A prerequisite edge is not an enrollment or a selected course. Rules and their raw provenance remain available together.
- Saved plans are snapshots with visibility and optimistic versioning; only applying them changes the active cart and popularity. Suppressed/unavailable popularity is not a measured zero.
- File records hold storage identity and verification state. Display/ownership policy is enforced by the backend; browser-stored signed URLs are not durable identity.
- Services differ in failure behavior. For example, the current moderation client can allow content when unavailable; the assistant returns explicit unavailability/provider errors. Do not promise a universal fail-closed policy that the code does not implement.
- School releases pair committed frontend/backend SHAs through the backend control branch. `main` and old axfff `production` workflows are different deployment paths. See [operations](operations/agent-boundaries.md).
- MeetCampus runtime is external. Its historical Alembic files remain because later revisions depend on the lineage. The independent CoursePlan service and TeamUp runtime are not owned by these Flask routes.

## Changing architecture

Update the affected feature reference and this page in the same change. For new structural decisions, add a dated decision with status, rationale, source/test links and consequences; do not copy an old plan's completion or production approval claim. [Maintenance policy](maintenance.md) defines documentation completion.
