# Scheduler saved plans API

Saved plans are named, concrete timetable snapshots. They are independent of the mutable scheduler cart: creating, reading, publishing, and cloning a plan never changes popularity. Applying a plan atomically replaces the user's cart for that semester and records the resulting popularity transitions.

Saved plans are excluded from anonymous course popularity counts regardless of visibility. Current course popularity is derived only from verified institutional users' carts, deduplicated by canonical email; counts from 1 through 4 are suppressed by the API.

## Visibility and privacy

- `private`: owner only; other viewers receive `404`.
- `unlisted`: readable by UUID link but omitted from discovery.
- `public`: readable by link and included in public discovery.
- `private_constraints.banned_periods` is returned only to the owner. It is never exposed or enforced when another user applies a shared plan.

## Routes

- `POST /scheduler/plans` — create a plan from course codes and exact bundle/layer selections.
- `GET /scheduler/plans/mine` — list the authenticated user's plans, optionally by `semester_id`.
- `GET /scheduler/plans/shared` — paginated public discovery, optionally by `semester_id` and `course_code`.
- `GET /scheduler/plans/<public_id>` — read an accessible plan.
- `PATCH /scheduler/plans/<public_id>` — owner update with required optimistic `version`.
- `DELETE /scheduler/plans/<public_id>` — owner soft delete.
- `POST /scheduler/plans/<public_id>/clone` — create an independent private copy.
- `POST /scheduler/plans/<public_id>/apply` — validate and atomically replace the semester workspace.
- `DELETE /scheduler/cart/<semester>` — start a blank workspace for the semester.

Availability is `current`, `updated`, or `unavailable`. Enrollment and quota movement does not stale a plan; only scheduling identity changes such as section metadata, meetings, rooms, or instructors produce `updated`. A removed/cancelled referenced section produces `unavailable` and cannot be applied.

## Operational limits

- 100 active plans per user.
- 20 courses per plan.
- 80 characters per name and 500 per description.
- Public identifiers are random UUIDs; database integer IDs are never exposed.
- The Alembic migration `20260822_scheduler_plans` is additive and performs no data backfill.
