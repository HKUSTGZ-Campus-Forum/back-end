# Courses, academic progress and scheduling

## Data model and authority

[Course](../../app/models/course.py) is canonical course identity. [course_domain.py](../../app/models/course_domain.py) separates catalog versions/requirements/edges, semester offerings/sections/meetings, user attempts/state and cart/selection records. The legacy scheduler models remain for migration/fallback paths; they are not interchangeable with canonical domain rows.

- PCC catalog content supplies titles, descriptions, prerequisite/corequisite/exclusion rules. [Relationship service](../../app/services/course_relationships.py) preserves raw text, classifies expressions and derives graph edges/downstream courses. [Course rule reference](../../COURSE_RELATIONSHIP_SOURCE_OF_TRUTH.md) describes precedence and the historical migration plan.
- SISN supplies semester offerings, availability, quota and meeting data. [SISN adaptation](../../app/services/sisn_offerings.py), [sync](../../app/services/sisn_sync.py) and [push authentication](../../app/services/sisn_push_auth.py) govern ingest. Preserve reviewed KLMS rows and source provenance where the official SISN feed cannot represent them.
- [Bundled data](../../app/data) and [pending packages](../../app/data/pending/README.md) are different. Pending files are not automatic startup inputs. Import preparation, dry-run, approved apply and service activation are separate actions.

## Course exploration and reviews

[Course routes](../../app/routes/course.py) resolve identifiers, return filters and overview, expose relationships and course/semester discussions/reviews. Overview and graph consume shared normalized rules. [Post routes](../../app/routes/post.py) bind reviews through `CoursePostOfferingTarget`; browsing all course reviews should not lose the offering context of each review.

## Academic progress

[Academic routes](../../app/routes/academic_map.py) manage profile, target majors, records/import and summary. [History parser](../../app/services/course_history_importer.py) parses pasted course history; [curriculum evaluator](../../app/services/academic_curriculum_evaluator.py) evaluates nested requirement trees and allocation; [summary service](../../app/services/academic_map_service.py) calculates the view. [Curriculum sync](../../app/services/academic_curriculum_sync.py) imports program/cohort rules. Preserve zero-credit courses, repeated attempts, source constraints and completed/in-progress distinctions; a title match is not sufficient evidence for a grade or requirement completion.

## Scheduler cart, plans and popularity

[Scheduler routes](../../app/routes/scheduler.py) provide semesters, subjects, search/detail, cart operations, popularity/history and legacy map data. [Policy](../../app/services/scheduler_policy.py) supplies credit/selection constraints; frontend solvers choose concrete compatible sections from those inputs.

[Saved-plan service](../../app/services/scheduler_plans.py), [routes](../../app/routes/scheduler_plan.py) and [models](../../app/models/scheduler_plan.py) implement snapshot, visibility, version conflicts, clone and apply. See [API contract](../scheduler-saved-plans-api.md). Save/read/publish/clone do not change the cart; apply validates current offerings and atomically replaces the semester cart. Private blocked periods are not disclosed or imposed on another user applying a shared plan.

[Popularity](../../app/services/scheduler_popularity.py) counts eligible verified institutional cart contributors with canonical email deduplication. Values 1–4 are suppressed. Saved plans are not contributions until applied. Historical sampling is currently scoped to the reviewed semester/universe and bounded sampling period in source; it is not a universal history feed for every semester. Missing/stale/suppressed history must not be manufactured as zero. Sampling CLIs are under [scripts](../../scripts); do not infer that a cron job is installed from code presence.

## Modify and verify

Change DTOs alongside serializers and frontend types/composables. Trace any import through canonical course matching, domain sync, reviewed control totals and duplicate reconciliation. Preserve Alembic history and seek the required environment-specific production data approval; old approval statements do not transfer to a new task.

Tests cover [relationships](../../tests/test_course_relationships.py), [domain services](../../tests/test_course_domain_services.py), [curriculum evaluator](../../tests/test_academic_curriculum_evaluator.py), [academic routes](../../tests/test_academic_map_routes.py), [scheduler routes](../../tests/test_scheduler_routes.py), [policy](../../tests/test_scheduler_policy.py), [popularity](../../tests/test_scheduler_popularity.py), [saved plans](../../tests/test_scheduler_plan_routes.py), [SISN](../../tests/test_sisn_automation.py) and the relevant import scripts. Calendar export is frontend-only and has no new Flask endpoint.
