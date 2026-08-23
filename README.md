# UniKorn Backend

UniKorn 的 Flask API 服务，负责认证、校园社区、课程目录、排课助手、学术地图、通知和管理后台等后端能力。生产环境使用 PostgreSQL、Redis 和 Gunicorn；本地前端默认通过 `http://localhost:3000/api` 代理到本服务的 `http://localhost:8000`。

## 本地开发

需要 Python 3.12、PostgreSQL 和 Redis。真实密钥只应放在本地环境或部署密钥中，不要写入仓库。

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
flask --app run.py db upgrade
python run.py
```

默认数据库连接为 `postgres:///app.db`，可通过 `DATABASE_URL` 覆盖。Redis 默认为 `redis://localhost:6379/0`，可通过 `REDIS_URL` 覆盖。启动后可访问 `http://localhost:8000/healthz` 检查服务状态。

## 测试

```bash
pytest -q
```

涉及数据库结构的改动必须同时提供 Alembic migration，并用空白 PostgreSQL 和现有数据库升级路径验证。

## 文档

- [后端运维 API](docs/backend-operations-api.md)
- [排课方案 API](docs/scheduler-saved-plans-api.md)
- [正式环境与发布边界](docs/production-environment.md)
- [学校 SSO](CAMPUS_SSO.md)
- [仓库协作规范](AGENTS.md)

正式服由后端仓库的 `school-production` 发布控制分支触发。该分支只修改 `deploy/school/school-production-release.json`，成对指定已经进入各自 `main` 的前后端完整 SHA；GitHub 验证通过后，学校服务器控制器会调用 `deploy/school/deploy-release.sh` 完成备份、迁移、健康检查和原子切换。包含 migration 或 `app/data` 产品数据变化时，manifest 必须记录用户已明确批准的迁移计划。不要使用旧 axfff production workflow 代替学校正式服发布流程。
