from decimal import Decimal

import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles

from app import create_app
from app.config import Config
from app.extensions import db
from app.models.academic_map import CurriculumProgram, CurriculumRequirementGroup, UserAcademicProfile, UserCourseRecord
from app.models.course import Course
from app.models.course_domain import (
    CourseCatalogRequirement,
    CourseCatalogVersion,
    CourseOffering,
    UserCourseAttempt,
    UserCourseState,
)
from app.models.user import User
from app.models.user_role import UserRole
from app.services.academic_map_service import build_academic_map_summary
from app.services.course_domain import derive_user_course_state, grade_points_for_letter


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
    course, _version, offering = add_domain_course(code, title=code, credits=units)
    if status in {UserCourseRecord.STATUS_COMPLETED, UserCourseRecord.STATUS_IN_PROGRESS}:
        attempt = UserCourseAttempt(
            user_id=user_id,
            course_id=course.id,
            offering_id=offering.id,
            status="completed" if status == UserCourseRecord.STATUS_COMPLETED else "in_progress",
            grade_letter=grade if keep_grade else None,
            grade_points=grade_points_for_letter(grade) if keep_grade else None,
            source="manual",
        )
        db.session.add(attempt)
        db.session.flush()
        state_data = derive_user_course_state(user_id, course.id)
        state = UserCourseState(user_id=user_id, course_id=course.id)
        for key, value in state_data.items():
            setattr(state, key, value)
        db.session.add(state)
        return attempt

    state = UserCourseState(
        user_id=user_id,
        course_id=course.id,
        status="interested",
        source="manual",
    )
    db.session.add(state)
    return state


def add_domain_course(code, title=None, credits=3, semester_id="2530"):
    normalized = code.replace(" ", "").upper()
    course = Course.query.filter_by(code=normalized).first()
    if course is None:
        course = Course(code=normalized, name=title or code, credits=credits)
    db.session.add(course)
    db.session.flush()
    course.normalized_code = course.normalized_code or normalized
    course.display_code = course.display_code or code
    course.canonical_title = title or course.canonical_title or code
    course.name = title or course.name
    course.credits = credits
    version = CourseCatalogVersion(
        course_id=course.id,
        source="test",
        source_version=semester_id,
        title=title or code,
        credits=credits,
        effective_from_semester_id=semester_id,
    )
    db.session.add(version)
    db.session.flush()
    offering = CourseOffering(
        course_id=course.id,
        semester_id=semester_id,
        catalog_version_id=version.id,
        offering_code=course.normalized_code,
        title_snapshot=title or code,
        credits_snapshot=credits,
        source="test",
        status="offered",
    )
    db.session.add(offering)
    db.session.flush()
    return course, version, offering


def add_domain_attempt(user_id, course, offering, status="completed", grade="A", points=4.0):
    attempt = UserCourseAttempt(
        user_id=user_id,
        course_id=course.id,
        offering_id=offering.id,
        status=status,
        grade_letter=grade,
        grade_points=points,
        source="manual",
    )
    db.session.add(attempt)
    db.session.flush()
    return attempt


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


def test_summary_uses_domain_course_state_attempts_and_best_grade(app):
    with app.app_context():
        create_user(114, "domain_attempts")
        db.session.add(UserAcademicProfile(user_id=114, cohort="2025", target_majors=["AI"]))
        course, _, first_offering = add_domain_course("AIAA2205", "Introduction to AI", credits=3, semester_id="2430")
        _, _, second_offering = add_domain_course("AIAA3300", "Other Course", credits=3, semester_id="2530")
        second_offering.course_id = course.id
        failed = add_domain_attempt(114, course, first_offering, status="completed", grade="F", points=0.0)
        passed = add_domain_attempt(114, course, second_offering, status="completed", grade="A-", points=3.7)
        db.session.add(UserCourseState(
            user_id=114,
            course_id=course.id,
            status="completed",
            best_attempt_id=passed.id,
            best_grade_points=3.7,
            best_grade_letter="A-",
            source="derived",
        ))
        db.session.commit()

        summary = build_academic_map_summary(114)

    assert summary["course_counts"]["completed"] == 1
    assert summary["credits"]["total_completed"] == 3
    assert summary["grade_metrics"]["ocga"]["status"] == "available"
    assert summary["grade_metrics"]["ocga"]["value"] == 3.7
    assert summary["records"][0]["course_code"] == "AIAA2205"
    assert summary["records"][0]["grade"] == "A-"


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


def test_requirement_matrix_uses_catalog_title_for_unimported_courses(app):
    with app.app_context():
        create_user(110, "matrix_catalog_title")
        program = CurriculumProgram(code="AI", name_en="Artificial Intelligence", cohort="2025", total_min_credits=120)
        db.session.add(program)
        db.session.flush()
        db.session.add(CurriculumRequirementGroup(
            program_id=program.id,
            key="fundamental_choice",
            name_en="Fundamental Choice",
            category="fundamental",
            min_courses=1,
            rule={"choices": ["UFUG1102"]},
            sort_order=1,
        ))
        db.session.add(UserAcademicProfile(user_id=110, cohort="2025", target_majors=["AI"]))
        db.session.commit()

        summary = build_academic_map_summary(110)

    cell = summary["requirement_matrix"][0]["rows"][0]["all_cells"][0]
    assert cell["course_code"] == "UFUG1102"
    assert cell["title"] == "Calculus I"


def test_requirement_matrix_returns_common_core_progress(app):
    with app.app_context():
        create_user(115, "common_core_progress")
        program = CurriculumProgram(code="AI", name_en="Artificial Intelligence", cohort="2025", total_min_credits=120)
        db.session.add(program)
        db.session.flush()
        db.session.add(CurriculumRequirementGroup(
            program_id=program.id,
            key="common_core",
            name_en="University Common Core Courses",
            category="common_core",
            min_credits=30,
            rule={
                "rule_tree": {
                    "type": "all_of",
                    "children": [
                        {
                            "type": "choose",
                            "key": "foundation_ctdl",
                            "label_en": "CTDL",
                            "min_courses": 1,
                            "min_credits": 3,
                            "courses": ["UCUG1000"],
                            "area": "CTDL",
                        },
                        {
                            "type": "choose",
                            "key": "broadening_arts",
                            "label_en": "Broadening: Arts",
                            "kind": "elective",
                            "min_credits": 3,
                            "courses": ["UCUG1500", "UCUG1600"],
                            "area": "A",
                            "constraints": [{"type": "course_area", "value": "A", "min_credits": 3}],
                        },
                    ],
                }
            },
            sort_order=1,
        ))
        db.session.add(UserAcademicProfile(user_id=115, cohort="2025", target_majors=["AI"]))
        add_record(115, "UCUG1000", status="completed", units=3)
        add_record(115, "UCUG1500", status="completed", units=3)
        add_record(115, "UCUG1600", status="completed", units=3)
        db.session.commit()

        summary = build_academic_map_summary(115)

    row = summary["requirement_matrix"][0]["rows"][0]
    assert row["key"] == "common_core"
    assert row["category"] == "common_core"
    assert row["current"]["counted_credits"] == 6
    assert row["current"]["required_credits"] == 6
    assert [section["key"] for section in row["sections"]] == ["foundation_ctdl", "broadening_arts"]
    broadening_cells = {cell["course_code"]: cell for cell in row["sections"][1]["cells"]}
    assert broadening_cells["UCUG1500"]["allocation_status"] == "counted"
    assert broadening_cells["UCUG1500"]["area"] == "A"
    assert broadening_cells["UCUG1600"]["allocation_status"] == "candidate"
    assert broadening_cells["UCUG1600"]["area"] == "H"


def test_requirement_matrix_uses_choice_minimum_for_progress(app):
    with app.app_context():
        create_user(108, "matrix_choice")
        program = CurriculumProgram(code="AI", name_en="Artificial Intelligence", cohort="2025", total_min_credits=120)
        db.session.add(program)
        db.session.flush()
        db.session.add(CurriculumRequirementGroup(
            program_id=program.id,
            key="ai_electives",
            name_en="AI Electives",
            category="major",
            min_courses=2,
            rule={"choices": ["AIAA2205", "AIAA3111", "AIAA3201", "DSAA2011"]},
            sort_order=1,
        ))
        db.session.add(UserAcademicProfile(user_id=108, cohort="2025", target_majors=["AI"]))
        add_record(108, "AIAA2205", status="completed")
        db.session.commit()

        summary = build_academic_map_summary(108)

    row = summary["requirement_matrix"][0]["rows"][0]
    assert row["progress_label"] == "1 / 2"
    assert len(row["all_cells"]) == 4


def test_requirement_matrix_returns_grouped_required_and_choice_sections(app):
    with app.app_context():
        create_user(109, "matrix_sections")
        program = CurriculumProgram(code="DSA", name_en="Data Science and Big Data Technology", cohort="2025", total_min_credits=120)
        db.session.add(program)
        db.session.flush()
        db.session.add(CurriculumRequirementGroup(
            program_id=program.id,
            key="major_required",
            name_en="Major Required Courses",
            category="major",
            min_courses=5,
            min_credits=15,
            rule={
                "required_courses": ["DSAA2012", "DSAA2031", "DSAA2043"],
                "choices": ["DSAA1001", "AIAA2205", "DSAA2011", "AIAA3111"],
            },
            sort_order=1,
        ))
        db.session.add(UserAcademicProfile(user_id=109, cohort="2025", target_majors=["DSA"]))
        add_record(109, "DSAA2012", status="completed")
        add_record(109, "DSAA2031", status="completed")
        add_record(109, "AIAA2205", status="in_progress")
        db.session.commit()

        summary = build_academic_map_summary(109)

    row = summary["requirement_matrix"][0]["rows"][0]
    assert row["progress_label"] == "3 / 5"
    assert len(row["sections"]) == 2
    assert row["sections"][0]["kind"] == "required"
    assert row["sections"][0]["required_count"] == 3
    assert row["sections"][0]["progress_label"] == "2 / 3"
    assert row["sections"][1]["kind"] == "choice"
    assert row["sections"][1]["required_count"] == 2
    assert row["sections"][1]["total_count"] == 4
    assert row["sections"][1]["progress_label"] == "1 / 2"
    assert row["sections"][1]["label_en"] == "Choose 2 of 4"
    assert row["sections"][1]["label_zh"] == "4 选 2"
    assert {cell["course_code"]: cell["status"] for cell in row["sections"][1]["cells"]}["DSAA1001"] == "choice"


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


def test_prerequisite_metrics_read_catalog_requirement_expressions(app):
    with app.app_context():
        create_user(115, "domain_prereq")
        db.session.add(UserAcademicProfile(user_id=115, cohort="2025", target_majors=["AI"]))
        prereq, _, prereq_offering = add_domain_course("UFUG2601", "Programming", credits=3)
        missing, _, _ = add_domain_course("MATH1001", "Math", credits=3)
        target, target_version, _ = add_domain_course("AIAA3201", "Computer Vision", credits=3)
        passed = add_domain_attempt(115, prereq, prereq_offering, status="completed", grade="A", points=4.0)
        db.session.add(UserCourseState(
            user_id=115,
            course_id=prereq.id,
            status="completed",
            best_attempt_id=passed.id,
            best_grade_points=4.0,
            best_grade_letter="A",
            source="derived",
        ))
        db.session.add(CourseCatalogRequirement(
            catalog_version_id=target_version.id,
            relation_type="prerequisite",
            raw_text="UFUG2601 AND MATH1001",
            normalized_text="UFUG2601 AND MATH1001",
            requirement_kind="course",
            expression_json={
                "op": "AND",
                "items": [
                    {"course_code": "UFUG2601"},
                    {"course_code": "MATH1001"},
                ],
            },
            source="test",
        ))
        db.session.commit()

        summary = build_academic_map_summary(115)

    assert summary["prerequisite_metrics"]["blocked_count"] == 1
    assert summary["prerequisite_metrics"]["blockers"][0]["course_code"] == "AIAA3201"
    assert summary["prerequisite_metrics"]["blockers"][0]["course_title"] == "Computer Vision"
    assert summary["prerequisite_metrics"]["blockers"][0]["missing"] == ["MATH1001"]


def test_requirement_matrix_returns_current_and_projected_rule_tree_progress(app):
    with app.app_context():
        create_user(111, "matrix_rule_tree")
        program = CurriculumProgram(code="DSA", name_en="Data Science and Big Data Technology", cohort="2025", total_min_credits=120)
        db.session.add(program)
        db.session.flush()
        db.session.add(CurriculumRequirementGroup(
            program_id=program.id,
            key="fundamental_courses",
            name_en="Fundamental Courses",
            name_zh="基础课程",
            category="fundamental",
            min_courses=2,
            min_credits=6,
            rule={
                "rule_tree": {
                    "type": "all_of",
                    "children": [
                        {"type": "choose", "key": "calculus_i", "min_courses": 1, "courses": ["UFUG1102", "UFUG1105"]},
                        {"type": "choose", "key": "calculus_ii", "min_courses": 1, "courses": ["UFUG1103", "UFUG1106"]},
                    ],
                }
            },
            sort_order=1,
        ))
        db.session.add(UserAcademicProfile(user_id=111, cohort="2025", target_majors=["DSA"]))
        add_record(111, "UFUG1105", status="completed")
        add_record(111, "UFUG1106", status="in_progress")
        db.session.commit()

        summary = build_academic_map_summary(111)

    row = summary["requirement_matrix"][0]["rows"][0]
    assert row["current"]["satisfied"] is True
    assert row["projected"]["satisfied"] is True
    assert [section["key"] for section in row["sections"]] == ["calculus_i", "calculus_ii"]
    assert row["sections"][1]["projected"]["counted_courses"] == 1


def test_requirement_matrix_prefers_catalog_credit_over_imported_units(app):
    with app.app_context():
        create_user(112, "matrix_catalog_credit")
        program = CurriculumProgram(code="AI", name_en="Artificial Intelligence", cohort="2025", total_min_credits=120)
        db.session.add(program)
        db.session.flush()
        db.session.add(CurriculumRequirementGroup(
            program_id=program.id,
            key="major_electives",
            name_en="Major Electives",
            category="major_elective",
            rule={
                "rule_tree": {
                    "type": "choose",
                    "key": "major_electives",
                    "kind": "elective",
                    "min_courses": 1,
                    "min_credits": 4,
                    "courses": ["UFUG2601"],
                }
            },
        ))
        db.session.add(UserAcademicProfile(user_id=112, cohort="2025", target_majors=["AI"]))
        add_record(112, "UFUG2601", status="completed", units=3)
        catalog_course = Course.query.filter_by(code="UFUG2601").one()
        catalog_course.credits = 4
        CourseCatalogVersion.query.filter_by(course_id=catalog_course.id).update({"credits": 4})
        db.session.commit()

        row = build_academic_map_summary(112)["requirement_matrix"][0]["rows"][0]

    cell = row["sections"][0]["cells"][0]
    assert row["current"]["counted_credits"] == 4
    assert cell["credits"] == 4
    assert cell["credit_source"] == "catalog"


def test_requirement_matrix_falls_back_to_imported_units_when_catalog_is_missing(app):
    with app.app_context():
        create_user(113, "matrix_record_credit")
        program = CurriculumProgram(code="AI", name_en="Artificial Intelligence", cohort="2025", total_min_credits=120)
        db.session.add(program)
        db.session.flush()
        db.session.add(CurriculumRequirementGroup(
            program_id=program.id,
            key="major_electives",
            name_en="Major Electives",
            category="major_elective",
            rule={
                "rule_tree": {
                    "type": "choose",
                    "key": "major_electives",
                    "kind": "elective",
                    "min_courses": 1,
                    "min_credits": 3,
                    "courses": ["TEST3000"],
                }
            },
        ))
        db.session.add(UserAcademicProfile(user_id=113, cohort="2025", target_majors=["AI"]))
        add_record(113, "TEST3000", status="completed", units=3)
        db.session.commit()

        row = build_academic_map_summary(113)["requirement_matrix"][0]["rows"][0]

    cell = row["sections"][0]["cells"][0]
    assert row["current"]["counted_credits"] == 3
    assert cell["credits"] == 3
    assert cell["credit_source"] == "catalog"
