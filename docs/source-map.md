# Backend source map

Static source inventory at `d02ce0e`, reconciled 2026-09-05. Start from the feature guide in [docs/README.md](README.md), then follow the route/service/model and relevant [tests](testing.md). Keep this map updated when modules are added or retired.

## Every registered route module

[register_blueprints](../app/routes/__init__.py) is the authority for registration. Prefixes below are Flask-side; public requests normally prepend `/api`. Route-level guards and serializers remain authoritative for permissions and payloads. [Full route declarations](api-routes.md) help locate a handler without importing the app.

| Module | Responsibility |
|---|---|
| [academic_map](../app/routes/academic_map.py) | Profile, course history import/records and evaluated academic progress |
| [admin](../app/routes/admin.py) | Admin overview, users/content moderation, summaries and audit |
| [agent](../app/routes/agent.py) | Assistant status, context, conversation history and chat |
| [analytics](../app/routes/analytics.py) | Public hot posts and summaries |
| [auth](../app/routes/auth.py) | Retired password routes, email verification, JWT refresh/logout and loaders |
| [background_tasks](../app/routes/background_tasks.py) | Worker status and embedding maintenance controls |
| [cache](../app/routes/cache.py) | Cache stats/maintenance and matching invalidation |
| [comment](../app/routes/comment.py) | Comment CRUD and post comment listing |
| [contest](../app/routes/contest.py) | Contest configuration, organizers, track submissions and export |
| [course](../app/routes/course.py) | Course discovery, canonical overview, relationships and semester discussions |
| [feedback](../app/routes/feedback.py) | Feedback publication, versions, comments and merge-request transitions |
| [feedback_admin](../app/routes/feedback_admin.py) | Administrative feedback/merge/comment decisions |
| [file](../app/routes/file.py) | Signed uploads, completion/callback, proxy/view/avatar and cleanup |
| [gugu](../app/routes/gugu.py) | Message/reply wall and deletion |
| [health](../app/routes/health.py) | Liveness and database/Redis readiness |
| [home_carousel](../app/routes/home_carousel.py) | Public carousel and admin slide lifecycle |
| [identity](../app/routes/identity.py) | Identity badge requests, review and display selection |
| [matching](../app/routes/matching.py) | Project/teammate recommendations, search and compatibility |
| [notification](../app/routes/notification.py) | Recipient notifications, unread count and read/delete state |
| [oauth](../app/routes/oauth.py) | UniKorn as OAuth provider and client management |
| [oidc](../app/routes/oidc.py) | UniKorn as school OIDC relying party and ticket exchange |
| [post](../app/routes/post.py) | Post CRUD, course-review targets and tag normalization |
| [profile](../app/routes/profile.py) | User matching profiles and embedding refresh |
| [project](../app/routes/project.py) | Project CRUD and recommendations |
| [project_interview](../app/routes/project_interview.py) | Model-assisted project description interview |
| [push](../app/routes/push.py) | Web push keys, subscriptions and delivery tests |
| [reaction](../app/routes/reaction.py) | Post/comment emoji reactions |
| [recruitment](../app/routes/recruitment.py) | Challenge config/runs, ranking and allowlisted admin overview |
| [scheduler](../app/routes/scheduler.py) | Semester/course/section/cart/popularity/map API and SISN ingest |
| [scheduler_plan](../app/routes/scheduler_plan.py) | Saved plan visibility, versioning, clone and apply |
| [search](../app/routes/search.py) | Post/user/tag/course/global search and previews |
| [tag](../app/routes/tag.py) | Tags and post associations |
| [user](../app/routes/user.py) | Profiles, onboarding, public identity and owned OAuth tokens |

## Persistence and domain services

[Model catalog](data-model.md) lists declared tables and their source. [Model registration](../app/models/__init__.py) and Alembic determine what is imported/migrated; a file on disk alone does not imply an exposed feature.

| Area | Service sources |
|---|---|
| Academic requirements/progress | [evaluator](../app/services/academic_curriculum_evaluator.py), [curriculum sync](../app/services/academic_curriculum_sync.py), [summary](../app/services/academic_map_service.py), [major metadata](../app/services/academic_major_metadata.py) |
| Catalog/rules/identity | [domain](../app/services/course_domain.py), [relationships](../app/services/course_relationships.py), [PCC sync](../app/services/official_course_catalog_sync.py), [bundled sync](../app/services/course_catalog_sync.py), [matcher](../app/services/course_catalog_matcher.py), [domain migration](../app/services/course_domain_migration.py), [history parser](../app/services/course_history_importer.py) |
| Scheduler/SISN | [plans](../app/services/scheduler_plans.py), [policy](../app/services/scheduler_policy.py), [popularity](../app/services/scheduler_popularity.py), [domain sync](../app/services/scheduler_domain_sync.py), [map seed](../app/services/scheduler_map_seed.py), [adaptation](../app/services/sisn_offerings.py), [sync](../app/services/sisn_sync.py), [proxy client](../app/services/sisn_proxy_client.py), [push auth](../app/services/sisn_push_auth.py) |
| Identity | [campus OIDC](../app/services/campus_oidc.py), [institutional email](../app/services/institutional_email.py), [email](../app/services/email_service.py) |
| Community/admin | [files](../app/services/file_service.py), [notifications](../app/services/notification_service.py), [push](../app/services/push_service.py), [moderation](../app/services/content_moderation_service.py), [feedback](../app/services/feedback_service.py), [audit](../app/services/admin_audit_service.py) |
| Cache/matching/model features | [cache](../app/services/cache_service.py), [matching cache](../app/services/matching_cache_service.py), [embeddings](../app/services/embedding_service.py), [matching](../app/services/matching_service.py), [project interview](../app/services/project_interview_service.py), [assistant](../app/services/agent_chat_service.py), [context](../app/services/agent_context_service.py), [recruitment](../app/services/recruitment_agent_service.py) |

## Remaining code and operational surfaces

| Path | Purpose / boundary |
|---|---|
| [app factory](../app/__init__.py), [extensions](../app/extensions.py), [config](../app/config.py) | Startup, framework instances, environment settings |
| [run.py](../run.py), [wsgi.py](../wsgi.py), [worker](../app/background_worker.py) | Local API-prefix-aware dev server, deployed WSGI, dedicated job process |
| [app/tasks](../app/tasks) | STS/upload cleanup, embeddings and optional PCC sync on shared APScheduler |
| [app/utils](../app/utils) | Permission checks, semester normalization, push validation, course-history text cleanup |
| [app/scripts](../app/scripts) | Imports, migrations/reconciliation, backups, fixed backend operation runner and historical initialization commands; presence is not authorization to run |
| [app/data](../app/data), [pending README](../app/data/pending/README.md) | Bundled authoritative/reviewed data, pending packages and operation allowlist; no runtime user data migration |
| [migrations](../migrations) | Alembic configuration, immutable historical revisions and version manifest; historical MeetCampus lineage remains |
| [scripts](../scripts) | Popularity sampling/cron entry points |
| [tools](../tools) | Release-manifest updates, storage/backups/permissions/deploy reconciliation and journal utilities |
| [deploy/school](../deploy/school/README.md) | School controller, Nginx/systemd, verified backup/migration/release/restore contracts |
| [.github/workflows](../.github/workflows) | PR CI, shared-dev deployment, school candidate/release validation, bounded operations and legacy axfff maintenance |
| [Dockerfile](../Dockerfile), [requirements](../requirements.txt), [development requirements](../requirements-dev.txt) | Build/runtime dependency contract; runtime/build lockfiles are maintained separately |
| [tests](../tests) | Unit, route, migration, tool and shell/deployment contract tests |

The root also retains one-off utilities (`add_club_tag.py`, `temp_identity_manager.py`) and OAuth HTML test clients. Inspect their target and side effects before use; they are not normal initialization instructions. Tracked IDE files, `.codex-venv` and `.codex-feedback-dev.db` are historical artifacts, not a reproducible development environment or a production data source. No calendar blueprint is registered despite retained calendar models. No MeetCampus runtime blueprint is registered.
