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


def _auto_init_contest():
    """
    应用启动时自动执行：
    - 若 contest_info 表为空，创建默认比赛记录
    - 若 UID=6（Mount）还不是 organizer，自动添加
    用 try/except 包裹，表不存在时静默跳过（迁移前的第一次启动）
    """
    try:
        from app.models.contest import ContestInfo
        from app.models.contest_organizer import ContestOrganizer
        from app.extensions import db
        from datetime import datetime, timezone

        ORGANIZER_UID = 6

        contest = ContestInfo.query.first()
        if not contest:
            contest = ContestInfo(
                title='百块奖金Web大赛',
                description='',
                rules='',
                prizes='',
                # 开始：2026-04-14 10:00 CST = UTC 02:00
                start_time=datetime(2026, 4, 14, 2, 0, 0, tzinfo=timezone.utc),
                # 结束：2026-04-21 00:00 CST = UTC 2026-04-20 16:00
                end_time=datetime(2026, 4, 20, 16, 0, 0, tzinfo=timezone.utc),
                is_active=False,
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
        # 表还不存在（迁移尚未执行）时静默跳过，不影响启动
        pass
