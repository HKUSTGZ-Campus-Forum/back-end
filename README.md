# UniKorn backend

Flask API for the UniKorn campus community: school authentication, forum and files, courses and scheduling, academic progress, notifications, administration, recruitment challenge and forum assistant.

- **Coding agents:** [AGENTS.md](AGENTS.md); **Claude:** [CLAUDE.md](CLAUDE.md).
- **Documentation:** [task index](docs/README.md), [architecture](docs/architecture.md), [source map](docs/source-map.md).
- **Develop:** [setup](docs/development.md), [testing](docs/testing.md).
- **Operate:** [production boundaries](docs/production-environment.md), [school release runbook](deploy/school/README.md).
- **Changes:** [changelog](CHANGELOG.md), [documentation archive](docs/history.md).

Use Python 3.12, PostgreSQL and Redis as specified by CI. Local execution needs explicit database/service configuration; do not point setup or test commands at production. See the development guide before creating the Flask app because configured startup helpers can write schema/data and start background jobs.

The frontend is the separate `HKUSTGZ-Campus-Forum/front-end` repository. Both `main` branches deploy shared dev; school production uses a reviewed backend `school-production` manifest pairing committed frontend/backend revisions. Source presence is not proof of a live deployment.
