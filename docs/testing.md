# Backend verification

Choose the narrowest test that detects the failure your change could introduce; broaden to the relevant suite and required integration gates. No service setup or test run is justified only because a command exists.

## Commands

Run from this repository in the established Python environment:

```bash
python -m pytest tests/test_api_prefix_contract.py -q
python -m pytest tests/test_scheduler_plan_routes.py -q
python -m pytest tests/ -q
```

The first two are examples: choose the actual affected files from [source map](source-map.md). The [CI workflow](../.github/workflows/backend-ci.yml) also runs dependency checks, immutable lock comparison and container checks; changes to runtime/build dependencies must account for those gates.

## Test routes

| Changed area | Relevant tests |
|---|---|
| App startup, worker, URL prefix, health | `test_runtime_safety.py`, `test_api_prefix_contract.py`, `test_deploy_health.py`, `test_container_runtime.py` |
| SSO, onboarding, token/account ownership | `test_campus_oidc.py`, `test_auth.py`, `test_user.py`, `test_oidc_legacy_recovery.py`, `test_email_integrity.py`, `test_oauth.py` |
| Forum, tags, course reviews, search | `test_post.py`, `test_post_tags.py`, `test_course_discussions.py`, `test_search_preview.py` |
| Uploads, avatars, push | `test_file_upload_flow.py`, `test_file_upload_status.py`, `test_avatar_delivery.py`, `test_push_security.py` |
| Courses, academic map, scheduler, SISN | `test_course_relationships.py`, `test_course_domain_services.py`, `test_academic_curriculum_evaluator.py`, `test_scheduler_routes.py`, `test_scheduler_popularity.py`, `test_scheduler_plan_routes.py`, `test_sisn_automation.py`; related import tests |
| Admin/feedback/carousel | `test_admin_console.py`, `test_identity_admin.py`, `test_feedback_admin.py`, feedback model/route/comment/merge tests, `test_home_carousel.py` |
| Assistant/recruitment | `test_agent_chat.py`, `test_recruitment.py` |
| School deployment/operations | `test_school_production_deployment.py`, `test_backend_operations.py`, `test_verified_database_backup.py`; affected shell/tool contracts |

## Migrations

Use a **dedicated empty disposable PostgreSQL database** for `PRISTINE_POSTGRES_DATABASE_URL`. The pristine fixture requires no existing public relations and writes schema and test rows. Never use an application database.

```bash
AUTO_INIT_ON_STARTUP=false ENABLE_BACKGROUND_TASKS=false   python -m pytest tests/test_pristine_postgres_migrations.py -q -rs
```

Set the database variable privately before this command. A skip because the variable is absent is not a pass. Also run revision-ID checks and migration-specific tests for affected tables, including feedback PostgreSQL coverage when applicable. Inspect the tests for their own database setup/teardown requirements. Read the immutable migration manifest and existing lineage before editing migrations.

## Documentation-only changes

Verify relative links (including case-sensitive Git paths), source path references, actual package/CI commands, archive recovery and diff scope. Do not start Flask against an unknown database to inspect routing. Use static route declarations and registration instead. No application runtime assurance follows from a docs link check.
