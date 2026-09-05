# Admin System Phase 1 Record

Date: 2026-06-07

## Completed

- Added `admin_audit_logs` as the first admin-specific database table.
- Added Alembic migration `20260607_admin_audit_logs.py`.
- Added startup auto-create support for `admin_audit_logs` in environments that skip migrations.
- Added unified admin console routes:
  - `GET /admin/overview`
  - `GET /admin/audit-logs`
  - `GET /admin/users`
  - `POST /admin/users/<id>/role`
  - `POST /admin/users/<id>/delete`
  - `POST /admin/users/<id>/restore`
  - `GET /admin/content/summary`
  - `GET /admin/content/posts`
  - `GET /admin/content/comments`
  - `POST /admin/content/posts/<id>/delete`
  - `POST /admin/content/posts/<id>/restore`
  - `POST /admin/content/comments/<id>/delete`
  - `POST /admin/content/comments/<id>/restore`

## Verification

- `pytest tests/test_admin_console.py -q`
- Result: 4 passed.

## Notes

- The overview endpoint intentionally returns grouped metrics so the frontend can render full-site health cards without many separate requests.
- Admin mutations now write audit rows for user role/deletion and content moderation actions.
- Academic Map summary is aggregate-only and does not expose private grades.

