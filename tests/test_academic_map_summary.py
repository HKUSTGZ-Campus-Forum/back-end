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


def test_requirement_matrix_orders_in_progress_before_completed(app):
    with app.app_context():
        create_user(103, "matrix_order")
        program = CurriculumProgram(code="AI", name_en="Artificial Intelligence", cohort="2025", total_min_credits=120)
        db.session.add(program)
        db.session.flush()
        db.session.add(CurriculumRequirementGroup(
            program_id=program.id,
            key="ai_core",
            name_en="AI Core",
            category="major",
            min_courses=3,
            rule={"required_courses": ["AIAA2205", "DSAA2011", "AIAA3111", "AIAA3201"]},
            sort_order=1,
        ))
        db.session.add(UserAcademicProfile(user_id=103, cohort="2025", target_majors=["AI"]))
        add_record(103, "AIAA2205", status="completed")
        add_record(103, "DSAA2011", status="completed")
        add_record(103, "AIAA3111", status="in_progress")
        db.session.commit()

        summary = build_academic_map_summary(103)

    row = summary["requirement_matrix"][0]["rows"][0]
    assert row["key"] == "ai_core"
    assert [cell["course_code"] for cell in row["visible_cells"][:3]] == ["AIAA3111", "AIAA2205", "DSAA2011"]
    assert row["progress_label"] == "3 / 4"


def test_shared_course_tags_use_target_majors(app):
    with app.app_context():
        create_user(104, "shared_tags")
        ai = CurriculumProgram(code="AI", name_en="Artificial Intelligence", cohort="2025", total_min_credits=120)
        dsa = CurriculumProgram(code="DSA", name_en="Data Science and Big Data Technology", cohort="2025", total_min_credits=120)
        db.session.add_all([ai, dsa])
        db.session.flush()
        db.session.add(CurriculumRequirementGroup(program_id=ai.id, key="core", name_en="Core", category="major", rule={"required_courses": ["DSAA2011"]}))
        db.session.add(CurriculumRequirementGroup(program_id=dsa.id, key="core", name_en="Core", category="major", rule={"required_courses": ["DSAA2011"]}))
        db.session.add(UserAcademicProfile(user_id=104, cohort="2025", target_majors=["AI", "DSA"]))
        add_record(104, "DSAA2011", status="completed")
        db.session.commit()

        summary = build_academic_map_summary(104)

    cell = summary["requirement_matrix"][0]["rows"][0]["visible_cells"][0]
    assert cell["shared_majors"] == ["AI", "DSA"]


def test_prerequisite_metrics_preserve_and_or_logic(app, monkeypatch):
    prerequisite_data = {
        "courses": [
            {
                "course_code": "AIAA3072",
                "course_title": "Foundations of Quantum Computing and Quantum AI",
                "prerequisite_expression": {
                    "op": "AND",
                    "items": [
                        {"op": "OR", "items": [{"course_code": "UFUG2102"}, {"course_code": "UFUG2103"}]},
                        {"course_code": "AIAA2711"},
                    ],
                },
            },
            {
                "course_code": "AIAA3201",
                "course_title": "Intro. to CV",
                "prerequisite_expression": {
                    "op": "OR",
                    "items": [{"course_code": "UFUG2601"}, {"course_code": "UFUG2602"}],
                },
            },
        ]
    }
    monkeypatch.setattr("app.services.academic_map_service._load_prerequisite_data", lambda: prerequisite_data)

    with app.app_context():
        create_user(105, "prereq_logic")
        db.session.add(UserAcademicProfile(user_id=105, cohort="2025", target_majors=["AI"]))
        add_record(105, "UFUG2601", status="completed")
        add_record(105, "UFUG2103", status="completed")
        db.session.commit()

        summary = build_academic_map_summary(105)

    assert summary["prerequisite_metrics"]["unlocked_count"] == 1
    assert summary["prerequisite_metrics"]["blocked_count"] == 1
    assert summary["prerequisite_metrics"]["blockers"][0]["course_code"] == "AIAA3072"
    assert summary["prerequisite_metrics"]["blockers"][0]["missing"] == ["AIAA2711"]
