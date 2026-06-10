import logging
import threading
from datetime import datetime, timezone
from flask import Flask
from flask import current_app
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from .config import Config
from .extensions import db, jwt, migrate, cache#, limiter
from .routes import register_blueprints
from app.tasks.sts_pool import init_pool_maintenance

logger = logging.getLogger(__name__)


@compiles(JSONB, "sqlite")
def _compile_jsonb_for_sqlite(type_, compiler, **kw):
    return "JSON"


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)
    _normalize_sqlite_engine_options(app)

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
    if app.config.get('AUTO_INIT_ON_STARTUP', True):
        with app.app_context():
            _auto_init_feedback_support()
            _auto_init_admin_support()
            _auto_init_academic_map_support()
            _auto_init_scheduler_support()
            _auto_init_course_domain_support()
            _auto_sync_course_catalog()
            _auto_sync_academic_curriculum()
            _auto_seed_scheduler_map()
            _auto_migrate_gugu_reply_columns()
            _auto_init_contest()
            _ensure_mount_admin_role()
            _seed_dev_feedback()

    # 25-26 春课表补丁：推迟到「首次 HTTP 请求」再跑，避免进程启动时数据库尚未就绪导致
    # 静默失败；幂等，多 worker 各执行一次可接受。
    _register_deferred_course_offerings_adjustments(app)
    _register_deferred_scheduler_offering_imports(app)

    return app


def _normalize_sqlite_engine_options(app):
    """Drop PostgreSQL-only connection options when tests override the URI to SQLite."""
    database_uri = app.config.get('SQLALCHEMY_DATABASE_URI') or ''
    if not database_uri.startswith('sqlite'):
        return

    engine_options = dict(app.config.get('SQLALCHEMY_ENGINE_OPTIONS') or {})
    connect_args = dict(engine_options.get('connect_args') or {})
    connect_args.pop('options', None)

    if connect_args:
        engine_options['connect_args'] = connect_args
    else:
        engine_options.pop('connect_args', None)

    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = engine_options


def _auto_init_feedback_support():
    """Ensure feedback tables exist and notifications can carry direct navigation URLs."""
    from sqlalchemy import inspect, text
    from app.models.feedback import Feedback
    from app.models.feedback_version import FeedbackVersion
    from app.models.feedback_merge_request import FeedbackMergeRequest
    from app.models.feedback_comment import FeedbackComment
    from app.models.feedback_merge_comment import FeedbackMergeComment
    from app.models.feedback_audit_event import FeedbackAuditEvent

    db.metadata.create_all(
        bind=db.engine,
        tables=[
            Feedback.__table__,
            FeedbackVersion.__table__,
            FeedbackMergeRequest.__table__,
            FeedbackComment.__table__,
            FeedbackMergeComment.__table__,
            FeedbackAuditEvent.__table__,
        ],
        checkfirst=True,
    )

    try:
        inspector = inspect(db.engine)
        if 'notifications' not in inspector.get_table_names():
            return

        existing = {c['name'] for c in inspector.get_columns('notifications')}
        if 'link_url' not in existing:
            with db.engine.connect() as conn:
                conn.execute(text("ALTER TABLE notifications ADD COLUMN link_url VARCHAR(255)"))
                conn.commit()
    except Exception:
        db.session.rollback()


def _auto_init_admin_support():
    """Ensure admin console audit tables exist when migrations are skipped."""
    from app.models.admin_audit_log import AdminAuditLog

    db.metadata.create_all(
        bind=db.engine,
        tables=[AdminAuditLog.__table__],
        checkfirst=True,
    )


def _auto_init_academic_map_support():
    """Ensure Academic Map tables exist on dev/prod servers even if migrations are skipped."""
    from app.models.academic_map import (
        CurriculumProgram,
        CurriculumRequirementGroup,
        UserAcademicProfile,
        UserCourseRecord,
    )

    db.metadata.create_all(
        bind=db.engine,
        tables=[
            CurriculumProgram.__table__,
            CurriculumRequirementGroup.__table__,
            UserAcademicProfile.__table__,
            UserCourseRecord.__table__,
        ],
        checkfirst=True,
    )


def _auto_init_scheduler_support():
    """Ensure scheduler columns and tables exist even if Alembic migrations are skipped."""
    from sqlalchemy import inspect, text
    from app.models.course import Course
    from app.models.scheduler_section import SchedulerSection
    from app.models.scheduler_lecture import SchedulerLecture
    from app.models.scheduler_map import SchedulerMapComponent, SchedulerMapLine
    from app.models.scheduler_cart import SchedulerUserCourseCart, SchedulerUserBundleCart

    db.metadata.create_all(bind=db.engine, tables=[Course.__table__], checkfirst=True)

    existing = {column['name'] for column in inspect(db.engine).get_columns('courses')}
    scheduler_columns = {
        'subject': 'VARCHAR(4)',
        'catalog_number': 'VARCHAR(16)',
        'course_title_abbr': 'VARCHAR(48)',
        'pre_requirement': 'TEXT',
        'co_requirement': 'TEXT',
        'exclusion': 'TEXT',
        'pg_course': 'BOOLEAN DEFAULT false',
        'klms_course': 'BOOLEAN DEFAULT false',
        'vector': 'VARCHAR(16)',
    }
    with db.engine.begin() as conn:
        for column_name, column_type in scheduler_columns.items():
            if column_name in existing:
                continue
            if db.engine.dialect.name == 'postgresql':
                conn.execute(text(
                    f'ALTER TABLE courses ADD COLUMN IF NOT EXISTS {column_name} {column_type}'
                ))
            else:
                conn.execute(text(f'ALTER TABLE courses ADD COLUMN {column_name} {column_type}'))

        if db.engine.dialect.name == 'postgresql':
            conn.execute(text('ALTER TABLE courses DROP CONSTRAINT IF EXISTS valid_credits'))
            conn.execute(text(
                'ALTER TABLE courses ADD CONSTRAINT valid_credits CHECK (credits >= 0)'
            ))

    db.metadata.create_all(
        bind=db.engine,
        tables=[
            SchedulerSection.__table__,
            SchedulerLecture.__table__,
            SchedulerMapComponent.__table__,
            SchedulerMapLine.__table__,
            SchedulerUserCourseCart.__table__,
            SchedulerUserBundleCart.__table__,
        ],
        checkfirst=True,
    )


def _auto_init_course_domain_support():
    """Ensure the redesigned course domain tables exist when migrations are skipped."""
    from sqlalchemy import inspect, text
    from app.models.course import Course
    from app.models.course_domain import (
        CourseCatalogVersion,
        CourseCatalogRequirement,
        CourseRequirementEdge,
        CourseOffering,
        CourseSection,
        CourseMeeting,
        UserCourseState,
        UserCourseAttempt,
        UserOfferingCart,
        UserSectionSelection,
        CoursePostOfferingTarget,
    )

    db.metadata.create_all(bind=db.engine, tables=[Course.__table__], checkfirst=True)

    existing = {column['name'] for column in inspect(db.engine).get_columns('courses')}
    canonical_columns = {
        'normalized_code': 'VARCHAR(32)',
        'display_code': 'VARCHAR(32)',
        'canonical_title': 'VARCHAR(255)',
    }
    with db.engine.begin() as conn:
        for column_name, column_type in canonical_columns.items():
            if column_name in existing:
                continue
            if db.engine.dialect.name == 'postgresql':
                conn.execute(text(
                    f'ALTER TABLE courses ADD COLUMN IF NOT EXISTS {column_name} {column_type}'
                ))
            else:
                conn.execute(text(f'ALTER TABLE courses ADD COLUMN {column_name} {column_type}'))

    db.metadata.create_all(
        bind=db.engine,
        tables=[
            CourseCatalogVersion.__table__,
            CourseCatalogRequirement.__table__,
            CourseRequirementEdge.__table__,
            CourseOffering.__table__,
            CourseSection.__table__,
            CourseMeeting.__table__,
            UserCourseAttempt.__table__,
            UserCourseState.__table__,
            UserOfferingCart.__table__,
            UserSectionSelection.__table__,
            CoursePostOfferingTarget.__table__,
        ],
        checkfirst=True,
    )


def _auto_sync_course_catalog():
    """Keep the Course table aligned with the bundled undergraduate catalog."""
    try:
        from app.models.course import Course
        from app.services.course_catalog_sync import sync_course_catalog_from_file

        db.metadata.create_all(bind=db.engine, tables=[Course.__table__], checkfirst=True)
        sync_course_catalog_from_file()
    except Exception:
        db.session.rollback()
        logger.exception("Failed to sync course catalog")


def _auto_sync_academic_curriculum():
    """Keep Academic Map curriculum requirements aligned with bundled official data."""
    if current_app.config.get("TESTING"):
        return
    try:
        from app.services.academic_curriculum_sync import sync_curriculum_requirements_from_file

        sync_curriculum_requirements_from_file()
    except Exception:
        db.session.rollback()
        logger.exception("Failed to sync academic curriculum requirements")


def _auto_seed_scheduler_map():
    """Seed bundled course universe map data only when the scheduler map is empty."""
    if current_app.config.get("TESTING"):
        return
    try:
        from app.services.scheduler_map_seed import seed_bundled_scheduler_map_if_empty

        result = seed_bundled_scheduler_map_if_empty()
        logger.info("Scheduler map seed result: %s", result)
    except Exception:
        db.session.rollback()
        logger.exception("Failed to seed scheduler map")


def _ensure_mount_admin_role():
    """Guarantee Mount (uid=6) keeps admin access on synced environments."""
    from sqlalchemy import inspect
    from app.models.user import User
    from app.models.user_role import UserRole

    inspector = inspect(db.engine)
    table_names = set(inspector.get_table_names())
    if 'users' not in table_names or 'user_roles' not in table_names:
        return

    mount_user = db.session.get(User, 6)
    if mount_user is None or mount_user.is_deleted:
        return

    admin_role = UserRole.query.filter_by(name=UserRole.ADMIN).first()
    if admin_role is None:
        admin_role = UserRole(name=UserRole.ADMIN, description='Administrator')
        db.session.add(admin_role)
        db.session.flush()

    if mount_user.role_id != admin_role.id:
        mount_user.role_id = admin_role.id
        db.session.commit()


def _seed_dev_feedback():
    """Create one published feedback record on dev for end-to-end smoke testing."""
    from sqlalchemy import inspect

    frontend_base_url = str(current_app.config.get('FRONTEND_BASE_URL', '')).lower()
    if 'dev.unikorn.axfff.com' not in frontend_base_url:
        return

    from app.models.user import User
    from app.models.feedback import Feedback
    from app.models.feedback_version import FeedbackVersion

    inspector = inspect(db.engine)
    table_names = set(inspector.get_table_names())
    required_tables = {'users', 'feedbacks', 'feedback_versions'}
    if not required_tables.issubset(table_names):
        return

    owner = db.session.get(User, 6)
    if owner is None or owner.is_deleted:
        return

    seed_title = '[DEV] Feedback flow smoke test'
    existing = Feedback.query.filter_by(author_id=owner.id, title=seed_title).first()
    if existing is not None:
        return

    feedback = Feedback(
        author_id=owner.id,
        title=seed_title,
        status=Feedback.STATUS_PUBLISHED,
        published_at=datetime.now(timezone.utc),
    )
    db.session.add(feedback)
    db.session.flush()

    version = FeedbackVersion(
        feedback_id=feedback.id,
        version_number=1,
        markdown_content=(
            '## DEV 测试反馈\n\n'
            '这是一条仅用于 dev 环境联调的反馈。\n\n'
            '- 用它测试公开列表、详情页和评论区\n'
            '- 用它发起 merge 申请，验证作者审批与管理员终审\n'
            '- 这条数据不会自动出现在生产环境'
        ),
        created_by_user_id=owner.id,
    )
    db.session.add(version)
    db.session.flush()

    feedback.current_version_id = version.id
    db.session.commit()


def _auto_migrate_gugu_reply_columns():
    """启动时自动补齐 gugu_messages.reply_to_message_id，兼容本地旧库。"""
    from sqlalchemy import inspect, text
    try:
        inspector = inspect(db.engine)
        if 'gugu_messages' not in inspector.get_table_names():
            return

        existing = {c['name'] for c in inspector.get_columns('gugu_messages')}
        if 'reply_to_message_id' not in existing:
            with db.engine.connect() as conn:
                conn.execute(text(
                    "ALTER TABLE gugu_messages ADD COLUMN reply_to_message_id INTEGER"
                ))
                conn.commit()
    except Exception:
        db.session.rollback()


def _auto_migrate_contest_submissions_track():
    """为 contest_submissions 增加 track 列，并为每名已有用户补全娱乐赛道占位行。"""
    from sqlalchemy import text, inspect
    try:
        inspector = inspect(db.engine)
        if 'contest_submissions' not in inspector.get_table_names():
            return
        cols = {c['name'] for c in inspector.get_columns('contest_submissions')}
        if 'track' not in cols:
            with db.engine.connect() as conn:
                conn.execute(text(
                    "ALTER TABLE contest_submissions ADD COLUMN track VARCHAR(20) NOT NULL DEFAULT 'tech'"
                ))
                conn.commit()
        from app.models.contest_submission import ContestSubmission, TRACK_TECH, TRACK_FUN
        _pn = '待提交'
        _desc = '已报名，等待提交作品'
        tech_uids = (
            db.session.query(ContestSubmission.user_id)
            .filter(ContestSubmission.track == TRACK_TECH)
            .distinct()
            .all()
        )
        for (uid,) in tech_uids:
            if ContestSubmission.query.filter_by(user_id=uid, track=TRACK_FUN).first():
                continue
            tech_row = ContestSubmission.query.filter_by(user_id=uid, track=TRACK_TECH).first()
            desc = (tech_row.description or _desc).strip() if tech_row else _desc
            db.session.add(ContestSubmission(
                user_id=uid,
                track=TRACK_FUN,
                project_name=_pn,
                description=desc,
                project_url=None,
                team_members=None,
            ))
        db.session.commit()
        try:
            with db.engine.connect() as conn:
                conn.execute(text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_contest_submissions_user_track "
                    "ON contest_submissions (user_id, track)"
                ))
                conn.commit()
        except Exception:
            pass
    except Exception:
        db.session.rollback()


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


def _apply_ufug_25_26_spring_adjustments():
    """幂等：UFUG 25-26 春（见 ``app/scripts/adjust_ufug_25_26_spring``）。失败时向外抛出。"""
    from app.scripts.adjust_ufug_25_26_spring import apply_ufug_25_26_spring_adjustments

    apply_ufug_25_26_spring_adjustments(dry_run=False, verbose=False)


def _apply_ucug_25_26_spring_adjustments():
    """幂等：UCUG 25-26 春。"""
    from app.scripts.adjust_ucug_25_26_spring import apply_ucug_25_26_spring_adjustments

    apply_ucug_25_26_spring_adjustments(dry_run=False, verbose=False)


def _apply_aiaa_25_26_spring_adjustments():
    """幂等：AIAA 25-26 春。"""
    from app.scripts.adjust_aiaa_25_26_spring import apply_aiaa_25_26_spring_adjustments

    apply_aiaa_25_26_spring_adjustments(dry_run=False, verbose=False)


def _register_deferred_course_offerings_adjustments(app: Flask):
    lock = threading.Lock()
    state = {"done": False, "failures": 0}
    max_failures = 30

    @app.before_request
    def _run_course_offerings_adjustments_once():
        if state["done"]:
            return
        with lock:
            if state["done"]:
                return
            try:
                _apply_ufug_25_26_spring_adjustments()
                _apply_ucug_25_26_spring_adjustments()
                _apply_aiaa_25_26_spring_adjustments()
                state["done"] = True
                state["failures"] = 0
            except Exception:
                db.session.rollback()
                state["failures"] += 1
                logger.exception(
                    "25-26 spring course DB adjustments failed (attempt %s/%s)",
                    state["failures"],
                    max_failures,
                )
                if state["failures"] >= max_failures:
                    state["done"] = True
                    logger.error(
                        "Giving up 25-26 spring course DB adjustments after %s failures; "
                        "check DB connectivity and logs.",
                        max_failures,
                    )


def _log_scheduler_import_result(label, result):
    plan = result.plan
    if plan is None:
        logger.info(
            "%s scheduler offering deploy update: status=%s mode=%s message=%s hash=%s",
            label,
            result.status,
            result.mode,
            result.message,
            result.import_hash,
        )
        return

    logger.info(
        "%s scheduler offering deploy update: "
        "status=%s mode=%s semester=%s courses=%s sections=%s lectures=%s "
        "zero_section_courses=%s replace_sections=%s replace_lectures=%s "
        "stale_cart_refs=%s hash=%s message=%s",
        label,
        result.status,
        result.mode,
        plan.semester_id,
        plan.courses,
        plan.sections,
        plan.lectures,
        len(plan.zero_section_courses),
        plan.existing_sections_to_replace,
        plan.existing_lectures_to_replace,
        len(plan.stale_cart_references),
        result.import_hash,
        result.message,
    )
    if plan.zero_section_courses:
        logger.info(
            "%s scheduler zero-section course codes: %s",
            label,
            ", ".join(plan.zero_section_courses),
        )
    if plan.stale_cart_references:
        logger.warning(
            "%s scheduler stale cart course codes after import: %s",
            label,
            ", ".join(plan.stale_cart_references),
        )


def _register_deferred_scheduler_offering_imports(app: Flask):
    if app.config.get("TESTING"):
        return

    lock = threading.Lock()
    state = {"done": False, "failures": 0}
    max_failures = 30

    @app.before_request
    def _run_scheduler_offering_imports_once():
        if state["done"]:
            return
        with lock:
            if state["done"]:
                return
            try:
                from app.scripts.import_scheduler_offerings import (
                    run_bundled_scheduler_offering_updates,
                )

                for update, result in run_bundled_scheduler_offering_updates():
                    _log_scheduler_import_result(update.label, result)
                state["done"] = True
                state["failures"] = 0
            except Exception:
                db.session.rollback()
                state["failures"] += 1
                logger.exception(
                    "Scheduler offering deploy update failed (attempt %s/%s)",
                    state["failures"],
                    max_failures,
                )
                if state["failures"] >= max_failures:
                    state["done"] = True
                    logger.error(
                        "Giving up scheduler offering deploy update after %s failures; "
                        "check DB connectivity and logs.",
                        max_failures,
                    )


def _auto_init_contest():
    """
    应用启动时自动执行：
    - 补齐数据库缺失列
    - 若 contest_info 表为空，创建默认比赛记录
    - 若 UID=6（Mount）还不是 organizer，自动添加
    用 try/except 包裹，表不存在时静默跳过
    """
    _auto_migrate_contest_columns()
    _auto_migrate_contest_submissions_track()

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
