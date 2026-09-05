# UniKorn Admin Backend Plan

## Phase 0 Audit

Current backend admin coverage:

- `/admin/feedbacks*`: feedback review, publication, rejection, close/reopen, comment state.
- `/admin/merge-requests*`: merge request final review.
- `/admin/feedback-comments/*/hide` and `/admin/feedback-merge-comments/*/hide`.
- `/identities/admin/*`: identity verification list, approve, reject, revoke.
- `/api/admin/cache/*`: cache and matching cache utilities.
- `/background-tasks/*`: task and embedding service status endpoints, some protected by admin checks.
- `/contest/*`: contest settings, submissions, organizers, with organizer/admin style checks.

Admin gaps:

- No unified admin overview endpoint.
- No unified audit log for admin mutations.
- User role/deletion management still requires direct scripts or ad hoc API calls.
- Forum/comment/tag/file/gugu moderation is not consolidated.
- Course, scheduler, Academic Map, matching, OAuth, notifications, push, file/STS, and background task health are scattered.
- Admin authorization is inconsistent in older routes.

## Database Changes Required

- Add `admin_audit_logs`:
  - `id`
  - `actor_user_id`
  - `action`
  - `target_type`
  - `target_id`
  - `target_label`
  - `note`
  - `metadata`
  - `created_at`
- Add indexes for actor, target, action, and created time.
- Future optional additions:
  - `admin_saved_filters` for per-admin console views.
  - `admin_system_events` for automated import/sync/runtime events.
  - `admin_content_flags` if user-facing reporting is added.

## Backend Phases

### Phase 1: Unified Admin Foundation

- Add `AdminAuditLog` model and auto-create support for environments that skip migrations.
- Add `/admin/overview` with metrics, pending work queues, and health cards.
- Add `/admin/audit-logs` with pagination/filtering.
- Add shared helpers for admin pagination, counts, and audit logging.

### Phase 2: Users And Content

- Add `/admin/users` with search, role, email verification, deleted filters.
- Add `/admin/users/<id>/role`, `/admin/users/<id>/delete`, `/admin/users/<id>/restore`.
- Add `/admin/content/summary`, `/admin/content/posts`, `/admin/content/comments`, `/admin/content/files`, `/admin/content/gugu`.
- Add post/comment/gugu soft-delete and restore actions.

### Phase 3: Domain Dashboards

- Add `/admin/courses/summary`, `/admin/courses/offerings`, `/admin/courses/import-health`.
- Add `/admin/academic-map/summary` with aggregate-only user record health.
- Add `/admin/matching/summary` and project/profile health.
- Add `/admin/contest/summary`.

### Phase 4: Operations

- Add `/admin/operations/summary` combining cache, STS/file, background task, OAuth, notification, and push metrics.
- Keep existing cache/background endpoints, but expose them through one admin surface.

## Backend Requirements

- Every admin mutation must write an audit log.
- Never expose private Academic Map grades in list responses; only aggregate counts.
- Keep existing public/user endpoints stable.
- Use existing soft-delete patterns where available.
- Add focused pytest coverage for authorization, metrics shape, audit logging, and primary mutations.

