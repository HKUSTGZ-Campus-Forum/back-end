from decimal import Decimal

import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles

from app import create_app
from app.config import Config
from app.extensions import db
from app.models.academic_map import CurriculumProgram, CurriculumRequirementGroup, UserAcademicProfile, UserCourseRecord
from app.models.user import User
from app.models.user_role import UserRole
from app.services.academic_map_service import build_academic_map_summary


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


def create_user(user_id, username):
    role = UserRole.query.filter_by(name=UserRole.USER).first()
    if role is None:
        role = UserRole(name=UserRole.USER, description="user role")
        db.session.add(role)
        db.session.flush()
    user = User(
        id=user_id,
        username=username,
        email=f"{username}@connect.hkust-gz.edu.cn",
        role_id=role.id,
        email_verified=True,
    )
    user.password_hash = "test-password-hash"
    db.session.add(user)
    return user


def add_record(user_id, code, status="completed", units=3, grade=None, keep_grade=False):
    record = UserCourseRecord(
        user_id=user_id,
        course_code=code,
        course_title=code,
        units=Decimal(str(units)),
        status=status,
        grade=grade,
        keep_grade=keep_grade,
        raw_payload={},
    )
    db.session.add(record)
    return record


def test_summary_returns_unuploaded_grade_metrics_when_no_private_grades(app):
    with app.app_context():
        create_user(101, "no_private_grades")
        profile = UserAcademicProfile(user_id=101, cohort="2025", target_majors=["AI"])
        db.session.add(profile)
        add_record(101, "AIAA2205", grade="A", keep_grade=False)
        db.session.commit()

        summary = build_academic_map_summary(101)

    assert summary["grade_metrics"]["ocga"]["status"] == "not_uploaded"
    assert summary["grade_metrics"]["ocga"]["value"] is None
    assert summary["grade_metrics"]["mcga"]["status"] == "not_uploaded"
    assert summary["grade_metrics"]["mcga"]["value"] is None


def test_summary_calculates_ocga_from_private_grade_records(app):
    with app.app_context():
        create_user(102, "private_grades")
        db.session.add(UserAcademicProfile(user_id=102, cohort="2025", target_majors=["AI"]))
        add_record(102, "AIAA2205", grade="A", keep_grade=True)
        add_record(102, "DSAA2011", grade="B+", keep_grade=True)
        add_record(102, "FTEC1010", grade="P", keep_grade=True)
        db.session.commit()

        summary = build_academic_map_summary(102)

    assert summary["grade_metrics"]["ocga"]["status"] == "available"
    assert summary["grade_metrics"]["ocga"]["value"] == 3.65
    assert summary["grade_metrics"]["ocga"]["included_courses"] == 2
    assert summary["grade_metrics"]["ocga"]["excluded_courses"] == 1
