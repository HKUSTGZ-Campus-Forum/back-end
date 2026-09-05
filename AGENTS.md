# UniKorn backend — agent instructions

This is the authoritative entry point for engineering in this repository. Claude users follow the same rules through [CLAUDE.md](CLAUDE.md).

## Start here

1. Inspect this checkout's branch, upstream and working changes. This repository has its own Git history; a parent workspace or neighboring frontend is not the same worktree. Preserve unrelated work. Use a scoped branch from the agreed code baseline.
2. Read [docs/README.md](docs/README.md), then the task's feature guide and source/test links. Read [architecture](docs/architecture.md) for cross-cutting changes.
3. Follow [documentation maintenance](docs/maintenance.md): **update the relevant docs in the same change as every notable modification**, and add notable behavior/technical changes to [CHANGELOG.md](CHANGELOG.md). Do not defer documentation to a later task.
4. Follow [operational boundaries](docs/operations/agent-boundaries.md). Before auth, deployment, migrations, Nginx, SISN or production data work, also read [production environment](docs/production-environment.md), [school runbook](deploy/school/README.md), and [SSO](CAMPUS_SSO.md).

## Implement

- Follow the Flask app factory, registered blueprints, SQLAlchemy models and existing service/task structure. [Source map](docs/source-map.md) identifies owners.
- Public `/api` is a proxy prefix; Flask routes omit it. Verify the route, frontend caller and proxy together.
- School OIDC is the only end-user login. Preserve JWT refresh, account linking, onboarding and token revocation. UniKorn's downstream OAuth provider is a different interface.
- Keep course catalog rules, semester offerings, academic records, scheduler carts and saved plans distinct. Preserve source provenance and popularity privacy rules.
- Schema changes belong in the existing Alembic lineage; do not use startup schema helpers as a production migration strategy. Keep external service calls out of tests using the established fixtures/mocks.
- MeetCampus has its own repository/runtime. Never add its removed runtime back. Do not alter the independent CoursePlan service as a side effect of UniKorn work.
- Keep changes small and relevant. Do not add speculative fallback layers or defensive machinery. Never expose credentials or live user data.

## Verify and hand off

Choose checks from [testing](docs/testing.md) based on the failure they detect. Run appropriate backend and migration checks for code changes; report missing services/dependencies and skipped checks truthfully. Check docs links and source claims for documentation-only changes.

Honor the user's authorized scope; do not repeat permission requests already settled in the task. Commit, push, PR, merge and deployment actions require applicable user authorization. Production schema/data approval must satisfy the explicit operational boundaries. Never infer production approval from a `main` push.

Report behavior changed, docs updated, checks/outcomes, and remaining limitations. Git archives and past plans are history, not instructions to execute.
