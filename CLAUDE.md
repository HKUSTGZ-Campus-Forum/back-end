# UniKorn backend instructions

Claude Code must follow [`AGENTS.md`](AGENTS.md). The current production target
is `https://unikorn.hkust-gz.edu.cn` on the school host; the axfff production
workflow is legacy and must not be treated as the active release path.
Routine production releases are triggered only by a manifest-only commit on the
backend `school-production` branch; `main` remains the shared-dev deployment branch.

Read [`docs/production-environment.md`](docs/production-environment.md),
[`deploy/school/README.md`](deploy/school/README.md), and
[`CAMPUS_SSO.md`](CAMPUS_SSO.md) before production, migration, authentication,
Nginx, or SISN work.
