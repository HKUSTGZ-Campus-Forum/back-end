# Admin System Phase 2 Record

## Scope

- Stabilized full backend verification after the admin console implementation.
- Kept production PostgreSQL behavior intact while making SQLite-based tests deterministic.
- Fixed legacy test fixtures that configured SQLite after `create_app()` had already attempted startup database initialization.

## Backend Changes

- Added `AUTO_INIT_ON_STARTUP` support so tests can opt out of startup data/bootstrap jobs.
- Added SQLite compilation support for PostgreSQL `JSONB` columns.
- Normalized PostgreSQL-only `connect_args.options` away when an app is configured for SQLite.
- Made project interview service import-safe when AI credentials are unavailable.
- Made OAuth token/code expiration checks tolerant of SQLite naive datetime round-trips.
- Made OAuth scope filtering preserve requested scope order.
- Updated legacy auth, gugu, post tag, AIAA adjustment, and OAuth model tests to use isolated SQLite configs.

## Verification

- `pytest tests/test_adjust_aiaa_25_26_spring.py tests/test_auth.py tests/test_post_tags.py tests/test_gugu.py tests/test_oauth_models.py -q`
- `pytest -q`

Result: full backend suite passed, 270 tests.
