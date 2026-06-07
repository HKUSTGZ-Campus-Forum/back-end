import pytest
from flask_jwt_extended import create_access_token
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles

from app import create_app
from app.config import Config
from app.extensions import db
from app.models.academic_map import UserAcademicProfile, UserCourseRecord
from app.models.course import Course
from app.models.course_domain import CourseOffering, UserCourseAttempt, UserCourseState
from app.services.course_catalog_matcher import find_course_by_normalized_code
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


@pytest.fixture
def client(app):
    return app.test_client()


def create_user_and_headers(app, username="route_user"):
    with app.app_context():
        role = UserRole.query.filter_by(name=UserRole.USER).first()
        if role is None:
            role = UserRole(name=UserRole.USER, description="user role")
            db.session.add(role)
            db.session.flush()
        user = User(username=username, email=f"{username}@connect.hkust-gz.edu.cn", role_id=role.id, email_verified=True)
        user.password_hash = "test-password-hash"
        db.session.add(user)
        db.session.commit()
        token = create_access_token(identity=str(user.id))
        return user.id, {"Authorization": f"Bearer {token}"}


def seed_offering(course, semester_id):
    offering = CourseOffering(
        course_id=course.id,
        semester_id=semester_id,
        offering_code=course.code,
        title_snapshot=course.name,
        credits_snapshot=course.credits,
        source="test",
        status="offered",
    )
    db.session.add(offering)
    db.session.flush()
    return offering


def test_catalog_matcher_prefers_canonical_domain_course_over_legacy_duplicate(app):
    with app.app_context():
        legacy = Course(code="DUPL 1001", name="Legacy Duplicate", credits=3)
        canonical = Course(
            code="DUPL1001",
            normalized_code="DUPL1001",
            display_code="DUPL 1001",
            name="Canonical Duplicate",
            credits=3,
        )
        db.session.add_all([legacy, canonical])
        db.session.flush()
        seed_offering(canonical, "2530")
        canonical_id = canonical.id
        db.session.commit()

        matched = find_course_by_normalized_code("DUPL 1001")

    assert matched.id == canonical_id


def test_update_profile_and_get_summary(client, app):
    _user_id, headers = create_user_and_headers(app)

    response = client.put("/academic-map/profile", json={"cohort": "2025", "target_majors": ["AI", "DSBD"]}, headers=headers)
    assert response.status_code == 200
    assert response.get_json()["profile"]["target_majors"] == ["AI", "DSA"]

    summary = client.get("/academic-map/summary", headers=headers)
    assert summary.status_code == 200
    assert summary.get_json()["profile"]["cohort"] == "2025"


def test_update_profile_normalizes_legacy_major_aliases(client, app):
    _user_id, headers = create_user_and_headers(app, "major_alias_user")

    response = client.put(
        "/academic-map/profile",
        json={"cohort": "2025", "target_majors": ["AI", "DSBD", "SEEN"]},
        headers=headers,
    )

    assert response.status_code == 200
    assert response.get_json()["profile"]["target_majors"] == ["AI", "DSA", "SEE"]


def test_parse_and_save_course_history_without_exposing_grade_publicly(client, app):
    _user_id, headers = create_user_and_headers(app, "import_user")
    pasted = "AIAA 2205 Introduction to AI 2024-25 Summer A+ 3.00"

    parse_response = client.post("/academic-map/import/parse", json={"text": pasted}, headers=headers)
    assert parse_response.status_code == 200
    parsed = parse_response.get_json()["rows"]
    assert parsed[0]["grade"] == "A+"

    save_response = client.post(
        "/academic-map/records/bulk",
        json={"keep_grades": True, "records": parsed},
        headers=headers,
    )
    assert save_response.status_code == 200
    records = save_response.get_json()["records"]
    assert records[0]["grade"] == "A+"

    delete_response = client.delete("/academic-map/grades", headers=headers)
    assert delete_response.status_code == 200
    assert delete_response.get_json()["cleared_count"] == 1


def test_parse_course_history_strips_copied_status_text(client, app):
    _user_id, headers = create_user_and_headers(app, "import_status_user")
    pasted = "AIAA 1010\tAcad. Orient for AI Ss\t2024-25 Fall\tP\t1.00\tTaken"

    parse_response = client.post("/academic-map/import/parse", json={"text": pasted}, headers=headers)

    assert parse_response.status_code == 200
    parsed = parse_response.get_json()["rows"]
    assert parsed[0]["course_title"] == "Academic Orientation for AI Students"
    assert parsed[0]["status"] == "completed"


def test_import_matches_course_catalog_by_normalized_code(client, app):
    user_id, headers = create_user_and_headers(app, "import_catalog_user")
    with app.app_context():
        course = Course.query.filter_by(code="AIAA1010").one()
        seed_offering(course, "2410")
        db.session.commit()

    pasted = "AIAA 1010\tAcad. Orient for AI Ss\t2024-25 Fall\tP\t1.00\tTaken"
    parse_response = client.post("/academic-map/import/parse", json={"text": pasted}, headers=headers)

    assert parse_response.status_code == 200
    parsed = parse_response.get_json()["rows"]
    assert parsed[0]["course_code"] == "AIAA1010"
    assert parsed[0]["matched_course_code"] == "AIAA1010"
    assert parsed[0]["course_title"] == "Academic Orientation for AI Students"
    assert parsed[0]["units"] == 1

    save_response = client.post(
        "/academic-map/records/bulk",
        json={"keep_grades": True, "records": parsed},
        headers=headers,
    )

    assert save_response.status_code == 200
    record = save_response.get_json()["records"][0]
    assert record["course_id"] is not None
    assert record["course_code"] == "AIAA1010"
    assert record["course_title"] == "Academic Orientation for AI Students"
    with app.app_context():
        course = Course.query.filter_by(code="AIAA1010").one()
        attempt = UserCourseAttempt.query.filter_by(user_id=user_id, course_id=course.id).one()
        assert attempt.status == "completed"
        assert attempt.offering.semester_id == "2410"
        state = UserCourseState.query.filter_by(user_id=user_id, course_id=course.id).one()
        assert state.status == "completed"

    summary_response = client.get("/academic-map/summary", headers=headers)
    assert summary_response.status_code == 200
    summary_records = summary_response.get_json()["records"]
    assert len(summary_records) == 1
    assert summary_records[0]["course_code"] == "AIAA1010"
    assert summary_records[0]["status"] == "completed"
    assert summary_records[0]["domain_state_id"] is not None
    assert summary_records[0]["domain_attempt_id"] is not None


def test_import_with_unresolved_offering_stays_visible_as_raw_record(client, app):
    _user_id, headers = create_user_and_headers(app, "import_missing_offering_user")
    with app.app_context():
        course = Course.query.filter_by(code="AIAA1010").one()
        assert CourseOffering.query.filter_by(course_id=course.id, semester_id="2410").count() == 0

    row = {
        "course_code": "AIAA1010",
        "matched_course_code": "AIAA1010",
        "course_title": "Academic Orientation for AI Students",
        "term_label": "2024-25 Fall",
        "term_code": "2410",
        "units": 1,
        "status": "completed",
        "grade": "P",
    }
    save_response = client.post(
        "/academic-map/records/bulk",
        json={"keep_grades": True, "records": [row]},
        headers=headers,
    )
    assert save_response.status_code == 200

    summary_response = client.get("/academic-map/summary", headers=headers)

    assert summary_response.status_code == 200
    records = summary_response.get_json()["records"]
    assert len(records) == 1
    assert records[0]["course_code"] == "AIAA1010"
    assert records[0]["term_code"] == "2410"
    assert records[0]["import_source"] == UserCourseRecord.SOURCE_PASTE


def test_record_update_and_delete_keep_domain_tables_in_sync(client, app):
    user_id, headers = create_user_and_headers(app, "record_sync_user")
    with app.app_context():
        course = Course.query.filter_by(code="AIAA1010").one()
        seed_offering(course, "2410")
        db.session.commit()

    row = {
        "course_code": "AIAA1010",
        "matched_course_code": "AIAA1010",
        "course_title": "Academic Orientation for AI Students",
        "term_label": "2024-25 Fall",
        "term_code": "2410",
        "units": 1,
        "status": "completed",
        "grade": "A",
    }
    save_response = client.post(
        "/academic-map/records/bulk",
        json={"keep_grades": True, "records": [row]},
        headers=headers,
    )
    assert save_response.status_code == 200
    record_id = save_response.get_json()["records"][0]["id"]

    update_response = client.put(
        f"/academic-map/records/{record_id}",
        json={"status": "in_progress"},
        headers=headers,
    )
    assert update_response.status_code == 200
    with app.app_context():
        course = Course.query.filter_by(code="AIAA1010").one()
        attempt = UserCourseAttempt.query.filter_by(user_id=user_id, course_id=course.id).one()
        state = UserCourseState.query.filter_by(user_id=user_id, course_id=course.id).one()
        assert attempt.status == "in_progress"
        assert attempt.grade_letter is None
        assert state.status == "in_progress"

    grade_response = client.put(
        f"/academic-map/records/{record_id}",
        json={"status": "completed", "keep_grade": True, "grade": "B+"},
        headers=headers,
    )
    assert grade_response.status_code == 200
    with app.app_context():
        course = Course.query.filter_by(code="AIAA1010").one()
        attempt = UserCourseAttempt.query.filter_by(user_id=user_id, course_id=course.id).one()
        state = UserCourseState.query.filter_by(user_id=user_id, course_id=course.id).one()
        assert attempt.status == "completed"
        assert attempt.grade_letter == "B+"
        assert state.status == "completed"
        assert str(state.best_grade_points) in {"3.30", "3.3"}

    delete_response = client.delete(f"/academic-map/records/{record_id}", headers=headers)
    assert delete_response.status_code == 204
    with app.app_context():
        course = Course.query.filter_by(code="AIAA1010").one()
        assert UserCourseAttempt.query.filter_by(user_id=user_id, course_id=course.id).count() == 0
        assert UserCourseState.query.filter_by(user_id=user_id, course_id=course.id).count() == 0


def test_import_can_match_catalog_from_title_when_code_is_missing(client, app):
    _user_id, headers = create_user_and_headers(app, "import_title_user")

    pasted = "Acad. Orient for AI Ss\t2024-25 Fall\tP\t1.00\tTaken"
    parse_response = client.post("/academic-map/import/parse", json={"text": pasted}, headers=headers)

    assert parse_response.status_code == 200
    parsed = parse_response.get_json()["rows"]
    assert parsed[0]["course_code"] == "AIAA1010"
    assert parsed[0]["matched_course_code"] == "AIAA1010"
    assert parsed[0]["course_title"] == "Academic Orientation for AI Students"


def test_clear_academic_map_records_for_current_user_only(client, app):
    user_id, headers = create_user_and_headers(app, "clear_records_user")
    other_user_id, _headers = create_user_and_headers(app, "clear_records_other")
    with app.app_context():
        db.session.add(UserAcademicProfile(user_id=user_id, cohort="2025", target_majors=["AI"]))
        db.session.add(UserCourseRecord(user_id=user_id, course_code="AIAA 1010", units=1))
        db.session.add(UserCourseRecord(user_id=other_user_id, course_code="AIAA 2205", units=3))
        db.session.commit()

    response = client.delete("/academic-map/records", headers=headers)

    assert response.status_code == 200
    assert response.get_json()["deleted_records"] == 1
    with app.app_context():
        assert UserCourseRecord.query.filter_by(user_id=user_id).count() == 0
        assert UserAcademicProfile.query.filter_by(user_id=user_id).one().cohort is None
        assert UserCourseRecord.query.filter_by(user_id=other_user_id).count() == 1


def test_mark_course_interested_creates_course_level_record(client, app):
    user_id, headers = create_user_and_headers(app, "interested_user")

    response = client.put("/academic-map/courses/AIAA2205/interest", json={}, headers=headers)

    assert response.status_code == 200
    record = response.get_json()["record"]
    assert record["status"] == "interested"
    assert record["course_code"] == "AIAA2205"
    assert record["course_title"] == "Introduction to Artificial Intelligence"
    assert record["term_label"] is None
    assert record["term_code"] is None
    assert record["course_id"] is not None
    with app.app_context():
        course = Course.query.filter_by(code="AIAA2205").one()
        state = UserCourseState.query.filter_by(user_id=user_id, course_id=course.id).one()
        assert state.status == "interested"


def test_mark_course_interested_does_not_overwrite_completed_record(client, app):
    user_id, headers = create_user_and_headers(app, "completed_interest_user")
    with app.app_context():
        course = Course.query.filter_by(code="AIAA2205").one()
        offering = seed_offering(course, "2410")
        attempt = UserCourseAttempt(
            user_id=user_id,
            course_id=course.id,
            offering_id=offering.id,
            status="completed",
            source="manual",
        )
        db.session.add(attempt)
        db.session.flush()
        db.session.add(UserCourseState(
            user_id=user_id,
            course_id=course.id,
            status="completed",
            best_attempt_id=attempt.id,
            source="derived",
        ))
        db.session.commit()

    response = client.put("/academic-map/courses/AIAA2205/interest", json={}, headers=headers)

    assert response.status_code == 409
    record = response.get_json()["record"]
    assert record["status"] == "completed"
    assert record["term_code"] == "2410"


def test_cancel_course_interested_deletes_only_interested_record(client, app):
    user_id, headers = create_user_and_headers(app, "cancel_interest_user")
    with app.app_context():
        course = Course.query.filter_by(code="AIAA2205").one()
        db.session.add(
            UserCourseRecord(
                user_id=user_id,
                course_id=course.id,
                course_code=course.code,
                course_title=course.name,
                status=UserCourseRecord.STATUS_INTERESTED,
            )
        )
        db.session.add(UserCourseState(
            user_id=user_id,
            course_id=course.id,
            status="interested",
            source="manual",
        ))
        db.session.add(
            UserCourseRecord(
                user_id=user_id,
                course_code="AIAA1010",
                course_title="Academic Orientation for AI Students",
                status=UserCourseRecord.STATUS_INTERESTED,
            )
        )
        db.session.commit()

    response = client.delete("/academic-map/courses/AIAA2205/interest", headers=headers)

    assert response.status_code == 200
    assert response.get_json()["deleted"] == 2
    with app.app_context():
        assert UserCourseRecord.query.filter_by(user_id=user_id, course_code="AIAA2205").count() == 0
        assert UserCourseRecord.query.filter_by(user_id=user_id, course_code="AIAA1010").count() == 1
        course = Course.query.filter_by(code="AIAA2205").one()
        assert UserCourseState.query.filter_by(user_id=user_id, course_id=course.id).count() == 0


def test_cancel_course_interested_does_not_delete_completed_record(client, app):
    user_id, headers = create_user_and_headers(app, "cancel_completed_user")
    with app.app_context():
        course = Course.query.filter_by(code="AIAA2205").one()
        db.session.add(
            UserCourseRecord(
                user_id=user_id,
                course_id=course.id,
                course_code=course.code,
                course_title=course.name,
                status=UserCourseRecord.STATUS_COMPLETED,
            )
        )
        db.session.commit()

    response = client.delete("/academic-map/courses/AIAA2205/interest", headers=headers)

    assert response.status_code == 200
    assert response.get_json()["deleted"] == 0
    with app.app_context():
        record = UserCourseRecord.query.filter_by(user_id=user_id, course_code="AIAA2205").one()
        assert record.status == UserCourseRecord.STATUS_COMPLETED
