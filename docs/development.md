# Backend development

Sources: [CI](../.github/workflows/backend-ci.yml), [requirements](../requirements-dev.txt), [configuration](../app/config.py), [factory](../app/__init__.py), [entry point](../run.py).

## Environment

Use Python 3.12. CI runs PostgreSQL 16 and a fresh virtual environment; most tests supply lightweight database/cache configurations and mock external services. A normal app can require PostgreSQL, Redis, OSS, SSO, mail, moderation and model providers. Do not assume those exist locally or copy a production environment file.

When setting up an explicitly requested local development environment:

```bash
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt
cp .env.example .env
```

Edit the new local `.env` to target a disposable local database and Redis. `DATABASE_URL` controls SQLAlchemy; the fallback `postgres:///app.db` is a PostgreSQL URL, not an SQLite file. Read `.env.example` and `app/config.py` for available keys; do not log their configured values.

`create_app()` can execute startup initialization and schedule jobs. Use `AUTO_INIT_ON_STARTUP=false` and `ENABLE_BACKGROUND_TASKS=false` while preparing a local database, running migration checks or importing the factory for inspection. Do not call it against an unknown target merely to list routes.

```bash
AUTO_INIT_ON_STARTUP=false ENABLE_BACKGROUND_TASKS=false flask --app run.py db upgrade
AUTO_INIT_ON_STARTUP=false ENABLE_BACKGROUND_TASKS=false python run.py
```

These commands are for a configured local environment. Inspect `run.py` for its listener; the frontend local configuration expects Flask on port 8000. Flask liveness is `/healthz`, readiness `/readyz`; a public proxy adds `/api`.

## Working layout

Routes validate requests and permissions; services hold reusable domain behavior; models own persistence and serializers. Register new routes in `app/routes/__init__.py`, import models through `app/models/__init__.py`, and use Alembic for schema changes. See [source map](source-map.md).

Use existing test configurations to isolate external calls. Do not bootstrap remote services to make a routine unit test pass. A fresh environment must satisfy `python -m pip check`; dependency changes also affect runtime locks and container CI.

Only the dedicated worker should own scheduled jobs in deployment. See [services/jobs](features/services.md) and the school runbook before changing worker configuration. See [testing](testing.md) for precise test choices.
