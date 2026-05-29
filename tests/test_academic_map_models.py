import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles

from app import create_app
from app.config import Config
from app.extensions import db
from app.models.user import User
from app.models.user_role import UserRole


@compiles(JSONB, "sqlite")
def compile_jsonb_sqlite(_type, _compiler, **_kw):
    return "JSON"


class TestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    CACHE_TYPE = "SimpleCache"
    ENABLE_BACKGROUND_TASKS = False
    JWT_SECRET_KEY = "test-secret"


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    for proxy_key in ["ALL_PROXY", "all_proxy", "HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"]:
        monkeypatch.delenv(proxy_key, raising=False)
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def create_user(username="academic_user"):
    role = UserRole.query.filter_by(name=UserRole.USER).first()
    if role is None:
        role = UserRole(name=UserRole.USER, description="user role")
        db.session.add(role)
        db.session.flush()
    user = User(username=username, email=f"{username}@connect.hkust-gz.edu.cn", role_id=role.id, email_verified=True)
    user.password_hash = "test-password-hash"
    db.session.add(user)
    db.session.flush()
    return user


def test_academic_profile_target_majors_are_editable(app):
    from app.models.academic_map import UserAcademicProfile

    user = create_user()
    profile = UserAcademicProfile.get_or_create_for_user(user.id)
    profile.cohort = "2025"
    profile.target_majors = ["AI", "DSBD"]
    db.session.commit()

    profile.target_majors = ["AI", "FTEC"]
    db.session.commit()

    reloaded = UserAcademicProfile.query.filter_by(user_id=user.id).one()
    assert reloaded.cohort == "2025"
    assert reloaded.target_majors == ["AI", "FTEC"]


def test_course_record_hides_grade_by_default(app):
    from app.models.academic_map import UserCourseRecord

    user = create_user("grade_private")
    record = UserCourseRecord(
        user_id=user.id,
        course_code="AIAA 2205",
        course_title="Introduction to AI",
        term_label="2024-25 Summer",
        units=3,
        status=UserCourseRecord.STATUS_COMPLETED,
        grade="A+",
        keep_grade=True,
    )
    db.session.add(record)
    db.session.commit()

    public_data = record.to_dict(include_grade=False)
    private_data = record.to_dict(include_grade=True)

    assert "grade" not in public_data
    assert private_data["grade"] == "A+"
    assert private_data["keep_grade"] is True


def test_academic_summary_counts_minimum_credit_progress(app):
    from app.models.academic_map import CurriculumProgram, UserAcademicProfile, UserCourseRecord
    from app.services.academic_map_service import build_academic_map_summary

    user = create_user("summary_user")
    program = CurriculumProgram(
        code="AI",
        name_en="BEng in Artificial Intelligence",
        name_zh="人工智能",
        cohort="2025",
        total_min_credits=120,
        common_core_min_credits=30,
        major_min_credits=85,
        home_areas=["Science", "Technology"],
    )
    db.session.add(program)
    profile = UserAcademicProfile.get_or_create_for_user(user.id)
    profile.cohort = "2025"
    profile.target_majors = ["AI"]
    db.session.add_all([
        UserCourseRecord(user_id=user.id, course_code="AIAA 2205", units=3, status=UserCourseRecord.STATUS_COMPLETED),
        UserCourseRecord(user_id=user.id, course_code="UCUG 1001", units=3, status=UserCourseRecord.STATUS_COMPLETED),
        UserCourseRecord(user_id=user.id, course_code="UFUG 2601", units=4, status=UserCourseRecord.STATUS_IN_PROGRESS),
    ])
    db.session.commit()

    summary = build_academic_map_summary(user.id)

    assert summary["profile"]["target_majors"] == ["AI"]
    assert summary["credits"]["total_completed"] == 6.0
    assert summary["credits"]["total_active"] == 10.0
    assert summary["credits"]["total_minimum"] == 120
    assert summary["credits"]["over_minimum"] is False
    assert summary["course_counts"]["imported"] == 3
