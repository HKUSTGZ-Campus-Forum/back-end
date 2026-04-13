from flask import Flask
from .config import Config
from .extensions import db, jwt, migrate, cache#, limiter
from .routes import register_blueprints
from app.tasks.sts_pool import init_pool_maintenance


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    # Initialize extensions
    db.init_app(app)
    jwt.init_app(app)
    cache.init_app(app)  # Initialize cache

    # Register blueprints (routes)
    register_blueprints(app)

    # # Create DB tables (for dev; in production use migrations)
    # with app.app_context():
    #     db.create_all()

    # Initialize in create_app()
    migrate.init_app(app, db)

    # limiter.init_app(app)

    # Initialize unified background task system (includes STS pool and embedding maintenance)
    init_pool_maintenance(app)

    # 启动时自动初始化比赛数据（幂等操作，重复执行安全）
    with app.app_context():
        _auto_init_contest()

    return app


def _auto_migrate_contest_columns():
    """启动时自动补齐 contest_info 表缺失的列（防止 flask db migrate 失败导致 500）"""
    from sqlalchemy import text, inspect
    try:
        with db.engine.connect() as conn:
            inspector = inspect(db.engine)
            if 'contest_info' not in inspector.get_table_names():
                return
            existing = {c['name'] for c in inspector.get_columns('contest_info')}
            if 'announcements' not in existing:
                conn.execute(text(
                    "ALTER TABLE contest_info ADD COLUMN announcements TEXT NOT NULL DEFAULT ''"
                ))
                conn.commit()
    except Exception:
        pass


def _merge_legacy_prizes_into_rules():
    """旧版「奖项设置」单独字段并入 rules（仅当 prizes 非空时执行一次，随后清空 prizes）"""
    try:
        from app.models.contest import ContestInfo
        contest = ContestInfo.query.first()
        if not contest or not (contest.prizes or '').strip():
            return
        block = contest.prizes.strip()
        if (contest.rules or '').strip():
            contest.rules = contest.rules.rstrip() + '\n\n## 奖项设置\n\n' + block
        else:
            contest.rules = block
        contest.prizes = ''
        db.session.commit()
    except Exception:
        db.session.rollback()


def _auto_init_contest():
    """
    应用启动时自动执行：
    - 补齐数据库缺失列
    - 若 contest_info 表为空，创建默认比赛记录
    - 若 UID=6（Mount）还不是 organizer，自动添加
    用 try/except 包裹，表不存在时静默跳过
    """
    _auto_migrate_contest_columns()

    try:
        from app.models.contest import ContestInfo
        from app.models.contest_organizer import ContestOrganizer
        from datetime import datetime, timezone

        ORGANIZER_UID = 6

        _merge_legacy_prizes_into_rules()

        contest = ContestInfo.query.first()
        if not contest:
            contest = ContestInfo(
                title='「百块奖金」校园生活 Web 开发大赛',
                description='想解锁校园版 Web 开发新体验？想用代码给校园生活加 buff？第一届「百块奖金」校园生活 Web 开发大赛来啦！主打一个技术玩出圈，创意贴校园～',
                rules='',
                prizes='',
                announcements='',
                start_time=datetime(2026, 4, 14, 2, 0, 0, tzinfo=timezone.utc),
                end_time=datetime(2026, 4, 20, 16, 0, 0, tzinfo=timezone.utc),
                is_active=True,
            )
            db.session.add(contest)
            db.session.flush()

        existing = ContestOrganizer.query.filter_by(
            contest_id=contest.id, user_id=ORGANIZER_UID
        ).first()
        if not existing:
            db.session.add(ContestOrganizer(contest_id=contest.id, user_id=ORGANIZER_UID))

        db.session.commit()
    except Exception:
        pass
