# Backend documentation index

Current implementation references, reconciled against backend `d02ce0e` and frontend `2c886f8` on 2026-09-05. This identifies the documentation baseline, not the deployed release. Check the current branch and code for subsequent changes.

## Choose a task

| Task | Read first | Follow through |
|---|---|---|
| New agent session | [AGENTS](../AGENTS.md), [maintenance](maintenance.md) | [architecture](architecture.md), [source map](source-map.md) |
| Run or test locally | [development](development.md) | [testing](testing.md), [configuration](../app/config.py) |
| Login, accounts, OAuth, identity | [auth and API](features/auth-api.md) | [SSO contract](../CAMPUS_SSO.md) |
| Posts, comments, tags, search, uploads, push | [community](features/community.md) | Source and tests in that guide |
| Courses, scheduler, academic map, imports | [academic systems](features/academic.md) | [course rules](../COURSE_RELATIONSHIP_SOURCE_OF_TRUTH.md), [saved plans API](scheduler-saved-plans-api.md) |
| Admin, feedback, contests, carousel | [administration](features/administration.md) | Guards, state transitions and audit tests |
| Forum assistant or recruitment | [AI features](features/ai.md) | [assistant API](agent-assistant-api.md) |
| Worker, cache, embeddings, matching | [services and jobs](features/services.md) | [source map](source-map.md) |
| Deploy, migrate, change production data | [operational boundaries](operations/agent-boundaries.md), [production environment](production-environment.md) | [school runbook](../deploy/school/README.md), [operations API](backend-operations-api.md), [pending data](../app/data/pending/README.md) |
| Creative spaces and private external repositories | [MakerSpace](features/makerspace.md) | Creator ownership, isolation, builds and publication review |
| External data exchange | [Directional approvals](features/makerspace-sync.md) | Adapter contract, credentials, retention, audit and closed-runtime requirements |
| Find an old design or document | [history and retrieval](history.md) | Git archive, never assumed current |

The current API contract is the registered route, service, serializer and caller together. The separate legacy `Docs` repositories and their OpenAPI YAML are historical; they do not cover the current route set. [Source map](source-map.md) covers every registered blueprint, plus models, services, migrations and tools.

For every notable modification, update this documentation with the code according to [maintenance](maintenance.md). Add new durable guides here. Do not reproduce environment secrets, mutable deployment SHAs, personal data or old approval statements as current facts.

For agents coordinating the enclosing multi-repository workspace, [workspace entry-point templates](workspace-entry-points.md) version the root routing instructions. These are templates, not an additional backend instruction hierarchy.

For new substantial work, use [the plan guide](plans/README.md).
