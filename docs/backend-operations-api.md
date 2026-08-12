# Backend operations API

Backend database operations are exposed through GitHub Actions' authenticated
`workflow_dispatch` REST API. They are not exposed as a Flask route: migrations
and data imports are long-running privileged work and must not execute inside a
Gunicorn request.

The workflow is `.github/workflows/backend-operations.yml`. A fine-grained
GitHub App or token used by an external operator needs only **Actions: write**
for this repository. Production SSH and database credentials remain GitHub
environment secrets and are never accepted in a request payload.

## Calling the API

The REST entry point is:

```text
POST /repos/HKUSTGZ-Campus-Forum/back-end/actions/workflows/backend-operations.yml/dispatches
```

For example, a production scheduler dry-run can be started with `gh`:

```bash
gh workflow run backend-operations.yml \
  --repo HKUSTGZ-Campus-Forum/back-end \
  --ref production \
  -f target=production \
  -f operation=scheduler-import \
  -f mode=dry-run \
  -f package_id=scheduler-2610-v1 \
  -f request_id=scheduler-2610-production-dry-run-1
```

The equivalent REST body is:

```json
{
  "ref": "production",
  "inputs": {
    "target": "production",
    "operation": "scheduler-import",
    "mode": "dry-run",
    "package_id": "scheduler-2610-v1",
    "request_id": "scheduler-2610-production-dry-run-1"
  }
}
```

Use `ref=main,target=dev` for dev and `ref=production,target=production`
for production. Any other ref/target combination is rejected.

The Python runner also recognizes `target=campus`, but the backend repository's
GitHub-hosted workflow intentionally does not transport campus requests. The
campus network is reached through the separately reviewed, root-owned controller
in the private `campus-deploy` repository. That controller runs this module from
the exact current backend image and supplies only the fixed arguments described
below. Until that controller is installed, a campus request has no executable
GitHub workflow route and therefore cannot report a false success.

## Allowlist

The initial operation allowlist is:

- `verify-release` in `dry-run` mode;
- `scheduler-import` with committed package `scheduler-2610-v1`;
- `curriculum-sync` with committed package `curriculum-2026-v1`;
- `course-duplicates` in dry-run or exact-control apply mode;
- `database-upgrade-heads` in apply mode, fixed to Alembic `heads`.

The campus target has a smaller allowlist: `verify-release`,
`scheduler-import`, and `curriculum-sync` only. Duplicate reconciliation and
database upgrades are rejected by the runner for campus even if it is invoked
outside GitHub Actions. A campus apply requires `confirmation=APPLY_CAMPUS`, a
verified backup digest, and a matching campus dry-run report; `APPLY_DEV` and
`APPLY_PRODUCTION` are not interchangeable with it.

The exact campus tuples are fixed in code: release verification is dry-run
only; scheduler package `scheduler-2610-v1` and curriculum package
`curriculum-2026-v1` each support dry-run and apply. Adding a later semester or
cohort to the package registry does not automatically enable it for campus.

Package paths, hashes, semester/cohort identifiers, and expected counts come
from `app/data/backend_operation_packages.json`. Callers cannot supply a path,
URL, database URL, SQL, command, Python module, migration revision, service
name, or destructive-import flag. Adding a future operation or package requires
a reviewed repository change.

## Apply protocol

Data-changing requests are separate workflow runs:

1. Dispatch and review a dry-run report.
2. Dispatch `apply` against the same branch SHA and package, passing the dry-run
   `request_id` as `approved_dry_run_id`.
3. For course reconciliation, also copy the exact database name, plan SHA-256,
   pair count, record count, and tag count from that dry-run.
4. Pass `confirmation=APPLY_DEV` or `confirmation=APPLY_PRODUCTION`.

Every apply refuses to run unless the server checkout is clean and exactly at
the dispatched SHA. It serializes operations with both GitHub concurrency and
`flock`, creates a PostgreSQL custom-format backup, verifies it with
`pg_restore --list`, records its SHA-256, and only then starts the operation.
The Python runner also acquires a PostgreSQL advisory lock and rebuilds a
scheduler or curriculum plan under that lock; its canonical result digest must
match the approved dry-run before mutation begins. After a successful apply, CI
restarts the service and checks both systemd and the local scheduler API.

Sanitized JSON reports are stored immutably under the target's
`operation-reports` directory using the request id; console logs additionally
include the GitHub run id. Reusing the same request id with an identical runner
request returns the existing result; a conflicting request is blocked without
overwriting the original report. GitHub run metadata and the verified backup
digest are execution evidence rather than logical request identity, so a safe
at-least-once retry can return the original result before creating another
backup or touching the database.

`database-upgrade-heads` has no meaningful dry-run and therefore does not take
an approved dry-run id, but it retains the confirmation, exact-SHA, clean-tree,
locking, verified-backup, and health gates.

## Campus controller contract

The private campus controller is responsible for transport and Docker lifecycle;
this repository remains responsible for validating and running the academic
operation. Its one-shot backend container must:

- use the current release's immutable backend image and pass that manifest's
  40-character backend commit as `--release-sha`;
- connect with the release's controlled backend/database environment and DB-only
  network, retain the image's non-root user and read-only root filesystem, and
  provide a private writable temporary directory;
- mount a root-controlled persistent report directory read/write and set
  `BACKEND_OPERATION_REPORT_DIR` to it, so an apply can verify the earlier
  campus dry-run after the one-shot container exits;
- construct the runner argument vector from a fixed operation table. It may
  forward validated request IDs and the exact fields already accepted by this
  runner, but must never accept a command, SQL, URL, path, database URL, module,
  migration revision, container service, or additional argument list;
- serialize against deployment/backup work, create and verify a database backup
  before apply, pass its SHA-256 as `--backup-sha256`, then restart and health
  check the current backend after a successful apply.

The Python runner adds a PostgreSQL advisory lock and independently repeats the
allowlist, confirmation, package digest, release/dry-run binding, and current
plan checks. The controller must not replace any of those checks.

## Security boundary

This workflow is the only manual-dispatch entry point for standalone database
operations. The two deployment workflows remain separate reviewed release
paths: they apply committed migrations and canonical startup data such as the
promoted curriculum bundle. Do not add a web endpoint that executes operations,
and do not generalize the runner into arbitrary shell or SQL. If a custom
webhook gateway is required later, it should only enqueue one of these versioned
operations after signature, timestamp, nonce, and idempotency checks; the
standalone database work must still run through this workflow.

The former direct scheduler-import and course-domain-migration dispatch
workflows were removed because they bypassed package hashes, exact-SHA checks,
shared locks, and dry-run/apply binding. Their underlying scripts remain in the
repository, but must only be re-exposed by adding a reviewed, deterministic
operation to this allowlist.
