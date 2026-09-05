# Shared services and jobs

## Cache and external services

[Extensions](../../app/extensions.py) defines SQLAlchemy/JWT/migrations/cache/OAuth instances; [configuration](../../app/config.py) selects concrete settings. [CacheService](../../app/services/cache_service.py) and [matching cache](../../app/services/matching_cache_service.py) serve different caching concerns. [Cache routes](../../app/routes/cache.py) expose guarded maintenance and profile invalidation; inspect owner/admin guards for the particular action before calling it.

[EmbeddingService](../../app/services/embedding_service.py) integrates DashScope and DashVector. [MatchingService](../../app/services/matching_service.py) uses profile/project embeddings and compatibility logic. Profile/project CRUD, matching recommendations and project-interview endpoints remain registered even though the frontend's `/matching` navigation redirects to TeamUp. A navigation migration did not delete these backend contracts. The `.removed` application-model artifact is not an active imported model.

Other adapters include OSS/STS, email, Aliyun content moderation, school OIDC, SISN and the assistant/recruitment providers. Their availability/error policy varies by caller; mock the relevant service in unit tests and do not claim credentials or connectivity exist because dependencies import successfully.

## Worker ownership

[app.background_worker](../../app/background_worker.py) creates the application, requires `ENABLE_BACKGROUND_TASKS=true`, verifies the scheduler started, and handles shutdown signals. [sts_pool](../../app/tasks/sts_pool.py) owns the shared APScheduler and registers:

- STS pool maintenance every 15 minutes.
- Hourly stale unbound upload cleanup, with the service's 24-hour recovery window.
- [Embedding maintenance](../../app/tasks/embedding_maintenance.py) for missing profile/project vectors.
- [Catalog sync](../../app/tasks/course_catalog_sync.py), gated by its configuration.

Web deployment must not run duplicate schedulers in each Gunicorn worker. Follow the existing single-worker service arrangement. Job wrappers enter app context, commit intended work and roll back failures. Add jobs through the existing registration point, not a new per-route scheduler.

Popularity history has separate [sampling scripts](../../scripts), and official SISN push/timers belong to the reviewed school/CoursePlan integration. They are not proven active by the APScheduler job list.

## Operations and verification

[Background-task routes](../../app/routes/background_tasks.py) provide status, embedding maintenance/stats and a health endpoint; route-level permission checks differ. A health response is not permission to trigger maintenance. Production cache flushes, imports and worker changes must follow [operational boundaries](../operations/agent-boundaries.md).

Tests: [runtime safety](../../tests/test_runtime_safety.py), [Redis cache](../../tests/test_redis_cache.py), [push security](../../tests/test_push_security.py), [container runtime](../../tests/test_container_runtime.py), [catalog sync](../../tests/test_course_catalog_sync.py), [popularity CLI](../../tests/test_scheduler_popularity_cli.py). Record missing integration coverage when a test suite does not exercise the external provider.
