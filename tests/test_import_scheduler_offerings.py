import json
import os

import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles

from app import create_app
from app.config import Config
from app.extensions import db
from app.models.course import Course
from app.models.course_domain import (
    CourseCatalogVersion,
    CourseMeeting,
    CourseOffering,
    CourseSection,
    UserOfferingCart,
    UserSectionSelection,
)
from app.models.scheduler_cart import SchedulerUserCourseCart
from app.models.scheduler_lecture import SchedulerLecture
from app.models.scheduler_section import SchedulerSection
from app.models.user import User
from app.models.user_role import UserRole
from app.scripts.import_scheduler_offerings import (
    DEPLOY_SCHEDULER_OFFERING_UPDATE_MODE,
    OfferingValidationError,
    SnapshotExpectations,
    apply_offerings,
    build_import_plan,
    bundled_scheduler_offering_updates,
    create_import_app,
    file_sha256,
    load_offerings_file,
    run_deploy_scheduler_offering_update,
    snapshot_counts,
)


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
    monkeypatch.setenv("OPENAI_API_KEY", os.getenv("OPENAI_API_KEY", "test-key"))
    monkeypatch.setenv("DASHSCOPE_API_KEY", os.getenv("DASHSCOPE_API_KEY", "test-key"))
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def write_payload(tmp_path, payload):
    path = tmp_path / "offerings.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def apply_snapshot(snapshot, **kwargs):
    return apply_offerings(
        snapshot,
        expected_counts=snapshot_counts(snapshot),
        **kwargs,
    )


def payload():
    return {
        "semester_id": "2540",
        "courses": [
            {
                "course_code": "TEST1001",
                "course_title": "Test Course",
                "course_desc": "",
                "credit": 3,
                "subject": "TEST",
                "catalog_number": "1001",
                "pg_course": False,
                "sections": [
                    {
                        "semester_id": "2540",
                        "section_id": "TEST1001-L01",
                        "course_code": "TEST1001",
                        "section_type": "L",
                        "name": "L01",
                        "bundle": 1,
                        "layer": 0,
                        "quota": 50,
                        "enrol": 47,
                        "avail": 3,
                        "wait": 2,
                        "is_main": True,
                        "lectures": [
                            {
                                "day": 1,
                                "start_time": "0900",
                                "end_time": "1050",
                                "room": "Room 101",
                                "instructor": "Dr. Test",
                            }
                        ],
                    }
                ],
            },
            {
                "course_code": "ZERO1001",
                "course_title": "Zero Section Course",
                "course_desc": "",
                "credit": 2,
                "subject": "ZERO",
                "catalog_number": "1001",
                "pg_course": False,
                "sections": [],
            },
        ],
    }


def test_load_offerings_file_validates_and_normalizes(tmp_path):
    snapshot = load_offerings_file(write_payload(tmp_path, payload()))

    assert snapshot.semester_id == "2540"
    assert len(snapshot.courses) == 2
    assert snapshot.courses[0].sections[0].lectures[0].start_time == 900


def test_create_import_app_maps_schema_query_to_postgres_search_path():
    flask_app = create_import_app(
        "postgresql://user:pass@localhost/course_scheduler?schema=public"
    )

    assert flask_app.config["SQLALCHEMY_DATABASE_URI"] == (
        "postgresql://user:pass@localhost/course_scheduler"
    )
    assert flask_app.config["SQLALCHEMY_ENGINE_OPTIONS"]["connect_args"]["options"] == (
        "-csearch_path=public"
    )


def test_load_offerings_file_rejects_invalid_lecture_day(tmp_path):
    data = payload()
    data["courses"][0]["sections"][0]["lectures"][0]["day"] = 8

    with pytest.raises(OfferingValidationError, match="expected 1-7"):
        load_offerings_file(write_payload(tmp_path, data))


@pytest.mark.parametrize(
    ("field_name", "invalid_time"),
    [
        ("start_time", "0960"),
        ("end_time", "1060"),
    ],
)
def test_load_offerings_file_rejects_invalid_hhmm_minutes(
    tmp_path,
    field_name,
    invalid_time,
):
    data = payload()
    data["courses"][0]["sections"][0]["lectures"][0][field_name] = invalid_time

    with pytest.raises(OfferingValidationError, match="expected valid HHMM time"):
        load_offerings_file(write_payload(tmp_path, data))


@pytest.mark.parametrize("field_name", ["credit", "quota", "bundle", "layer"])
def test_load_offerings_file_rejects_negative_scheduler_numbers(tmp_path, field_name):
    data = payload()
    if field_name == "credit":
        data["courses"][0][field_name] = -1
    else:
        data["courses"][0]["sections"][0][field_name] = -1

    with pytest.raises(OfferingValidationError, match="expected non-negative integer"):
        load_offerings_file(write_payload(tmp_path, data))


def test_load_offerings_file_accepts_zero_scheduler_numbers(tmp_path):
    data = payload()
    data["courses"][0]["credit"] = 0
    data["courses"][0]["sections"][0].update({
        "quota": 0,
        "bundle": 0,
        "layer": 0,
    })

    snapshot = load_offerings_file(write_payload(tmp_path, data))

    assert snapshot.courses[0].credit == 0
    section = snapshot.courses[0].sections[0]
    assert (section.quota, section.bundle, section.layer) == (0, 0, 0)


@pytest.mark.parametrize(
    ("field_name", "value"),
    [("enrol", -1), ("wait", -2)],
)
def test_load_offerings_file_rejects_invalid_optional_occupancy(
    tmp_path,
    field_name,
    value,
):
    data = payload()
    data["courses"][0]["sections"][0][field_name] = value

    with pytest.raises(OfferingValidationError, match="greater than or equal"):
        load_offerings_file(write_payload(tmp_path, data))


def test_load_offerings_file_preserves_occupancy_sentinels(tmp_path):
    data = payload()
    data["courses"][0]["sections"][0].update({"avail": -3, "wait": -1})

    snapshot = load_offerings_file(write_payload(tmp_path, data))

    section = snapshot.courses[0].sections[0]
    assert (section.avail, section.wait) == (-3, -1)


def test_apply_offerings_preserves_section_occupancy(app, tmp_path):
    snapshot = load_offerings_file(write_payload(tmp_path, payload()))

    with app.app_context():
        apply_snapshot(snapshot)

        section = (
            CourseSection.query.join(CourseOffering)
            .filter(CourseOffering.semester_id == "2540")
            .one()
        )
        assert (section.quota, section.enrol, section.avail, section.wait) == (
            50,
            47,
            3,
            2,
        )


def test_load_offerings_file_rejects_empty_snapshot(tmp_path):
    with pytest.raises(OfferingValidationError, match="must not be empty"):
        load_offerings_file(write_payload(tmp_path, {
            "semester_id": "2540",
            "courses": [],
        }))


def test_apply_offerings_requires_reviewed_counts_for_fresh_semester(app, tmp_path):
    snapshot = load_offerings_file(write_payload(tmp_path, payload()))
    reviewed_counts = SnapshotExpectations(
        courses=3,
        offered_courses=2,
        sections=2,
        lectures=2,
    )

    with app.app_context():
        with pytest.raises(OfferingValidationError, match="reviewed snapshot counts are required"):
            apply_offerings(snapshot)
        with pytest.raises(OfferingValidationError, match="independently reviewed counts"):
            apply_offerings(snapshot, expected_counts=reviewed_counts)

        assert Course.query.filter(Course.code.in_(["TEST1001", "ZERO1001"])).count() == 0
        assert CourseOffering.query.filter_by(semester_id="2540").count() == 0
        assert SchedulerSection.query.filter_by(semester_id="2540").count() == 0


def test_bundled_scheduler_offering_updates_match_files():
    updates = bundled_scheduler_offering_updates()

    assert DEPLOY_SCHEDULER_OFFERING_UPDATE_MODE == "apply"
    assert [update.expected_semester_id for update in updates] == ["2510", "2530", "2540"]
    for update in updates:
        snapshot = load_offerings_file(update.file_path, update.expected_semester_id)
        assert snapshot.semester_id == update.expected_semester_id
        assert file_sha256(update.file_path) == update.expected_sha256
        assert snapshot_counts(snapshot) == update.expected_counts


def test_build_plan_and_apply_replace_only_target_semester(app, tmp_path):
    snapshot = load_offerings_file(write_payload(tmp_path, payload()))

    with app.app_context():
        old = Course(code="OLD1001", name="Old Course", credits=3)
        other_semester = Course(code="KEEP1001", name="Keep Course", credits=3)
        db.session.add_all([old, other_semester])
        db.session.flush()
        db.session.add_all([
            SchedulerSection(
                semester_id="2540",
                section_id="OLD-L01",
                course_id=old.id,
                name="L01",
                bundle=1,
                layer=0,
                quota=10,
                section_type="L",
                is_main=True,
            ),
            SchedulerSection(
                semester_id="2530",
                section_id="KEEP-L01",
                course_id=other_semester.id,
                name="L01",
                bundle=1,
                layer=0,
                quota=10,
                section_type="L",
                is_main=True,
            ),
        ])
        db.session.add(SchedulerLecture(
            semester_id="2540",
            section_id="OLD-L01",
            day=1,
            start_time=900,
            end_time=1000,
            room="Old Room",
            instructor="Old Instructor",
        ))
        old_offering = CourseOffering(
            course_id=old.id,
            semester_id="2540",
            offering_code="OLD1001",
            title_snapshot="Old Course",
            credits_snapshot=3,
            source="test",
            status="offered",
        )
        keep_offering = CourseOffering(
            course_id=other_semester.id,
            semester_id="2530",
            offering_code="KEEP1001",
            title_snapshot="Keep Course",
            credits_snapshot=3,
            source="test",
            status="offered",
        )
        db.session.add_all([old_offering, keep_offering])
        db.session.flush()
        old_domain_section = CourseSection(
            offering_id=old_offering.id,
            source_section_id="OLD-L01",
            name="L01",
            section_type="L",
            bundle=1,
            layer=0,
            quota=10,
            is_main=True,
        )
        keep_domain_section = CourseSection(
            offering_id=keep_offering.id,
            source_section_id="KEEP-L01",
            name="L01",
            section_type="L",
            bundle=1,
            layer=0,
            quota=10,
            is_main=True,
        )
        db.session.add_all([old_domain_section, keep_domain_section])
        db.session.flush()
        db.session.add_all([
            CourseMeeting(
                section_id=old_domain_section.id,
                day=1,
                start_time=900,
                end_time=1000,
                room="Old Room",
                instructor_text="Old Instructor",
            ),
            CourseMeeting(
                section_id=keep_domain_section.id,
                day=2,
                start_time=900,
                end_time=1000,
                room="Keep Room",
                instructor_text="Keep Instructor",
            ),
        ])
        role = UserRole.query.filter_by(name="user").first() or UserRole(name="user")
        db.session.add(role)
        db.session.flush()
        user = User(
            username="scheduler_import_user",
            email="scheduler_import@hkust-gz.edu.cn",
            email_verified=True,
            role_id=role.id,
        )
        user.set_password("password123")
        db.session.add(user)
        db.session.flush()
        db.session.add(SchedulerUserCourseCart(
            user_id=user.id,
            semester_id="2540",
            course_code="OLD1001",
            enabled=True,
        ))
        db.session.commit()

        plan = build_import_plan(snapshot)
        assert plan.existing_sections_to_replace == 1
        assert plan.existing_lectures_to_replace == 1
        assert plan.course_rows_to_insert == 2
        assert plan.stale_cart_references == ["OLD1001"]
        assert plan.omitted_offering_codes == ["OLD1001"]
        assert plan.omitted_section_keys == ["OLD1001/OLD-L01"]

        apply_snapshot(snapshot, allow_destructive_replacement=True)

        assert Course.query.filter_by(code="TEST1001").one().name == "Test Course"
        assert Course.query.filter_by(code="ZERO1001").one().credits == 2
        test_course = Course.query.filter_by(code="TEST1001").one()
        assert test_course.normalized_code == "TEST1001"
        assert test_course.display_code == "TEST 1001"
        assert CourseCatalogVersion.query.filter_by(
            course_id=test_course.id,
            source="scheduler_offerings",
            source_version="2540",
        ).one().title == "Test Course"
        offering = CourseOffering.query.filter_by(
            course_id=test_course.id,
            semester_id="2540",
        ).one()
        assert offering.title_snapshot == "Test Course"
        assert CourseSection.query.filter_by(offering_id=offering.id).count() == 1
        assert CourseMeeting.query.join(CourseSection).filter(
            CourseSection.offering_id == offering.id
        ).one().instructor_text == "Dr. Test"
        zero_course = Course.query.filter_by(code="ZERO1001").one()
        assert CourseOffering.query.filter_by(course_id=zero_course.id, semester_id="2540").count() == 0
        assert old_offering.status == "archived"
        assert CourseSection.query.filter_by(offering_id=old_offering.id).count() == 0
        assert CourseSection.query.filter_by(offering_id=keep_offering.id).count() == 1
        assert SchedulerSection.query.filter_by(semester_id="2540").count() == 1
        assert SchedulerLecture.query.filter_by(semester_id="2540").count() == 1
        assert SchedulerSection.query.filter_by(semester_id="2530", section_id="KEEP-L01").one()
        assert SchedulerUserCourseCart.query.filter_by(
            user_id=user.id,
            semester_id="2540",
            course_code="OLD1001",
        ).one()


def test_apply_offerings_rejects_incomplete_snapshot_before_mutation(app, tmp_path):
    full_snapshot = load_offerings_file(write_payload(tmp_path, payload()))

    with app.app_context():
        apply_snapshot(full_snapshot)
        course = Course.query.filter_by(code="TEST1001").one()
        offering = CourseOffering.query.filter_by(
            course_id=course.id,
            semester_id="2540",
        ).one()
        section = CourseSection.query.filter_by(offering_id=offering.id).one()
        meeting = CourseMeeting.query.filter_by(section_id=section.id).one()

        incomplete_payload = payload()
        incomplete_payload["courses"] = [incomplete_payload["courses"][1]]
        incomplete_snapshot = load_offerings_file(write_payload(tmp_path, incomplete_payload))

        with pytest.raises(OfferingValidationError, match="refusing destructive replacement"):
            apply_snapshot(incomplete_snapshot)

        db.session.expire_all()
        assert db.session.get(CourseOffering, offering.id).status == "offered"
        assert db.session.get(CourseSection, section.id) is not None
        assert db.session.get(CourseMeeting, meeting.id) is not None
        assert SchedulerSection.query.filter_by(
            semester_id="2540",
            section_id="TEST1001-L01",
        ).one()
        assert SchedulerLecture.query.filter_by(
            semester_id="2540",
            section_id="TEST1001-L01",
        ).one()


def test_apply_offerings_rejects_omitted_meetings_before_mutation(app, tmp_path):
    full_snapshot = load_offerings_file(write_payload(tmp_path, payload()))

    with app.app_context():
        apply_snapshot(full_snapshot)
        partial_payload = payload()
        partial_payload["courses"][0]["sections"][0]["lectures"] = []
        partial_snapshot = load_offerings_file(write_payload(tmp_path, partial_payload))
        plan = build_import_plan(partial_snapshot)

        assert plan.omitted_offering_codes == []
        assert plan.omitted_section_keys == []
        assert len(plan.omitted_meeting_keys) == 1

        with pytest.raises(OfferingValidationError, match="meetings="):
            apply_snapshot(partial_snapshot)

        assert SchedulerLecture.query.filter_by(semester_id="2540").count() == 1
        assert CourseMeeting.query.join(CourseSection).join(CourseOffering).filter(
            CourseOffering.semester_id == "2540"
        ).count() == 1


def test_apply_offerings_reuses_normalized_course_row(app, tmp_path):
    snapshot = load_offerings_file(write_payload(tmp_path, payload()))

    with app.app_context():
        existing = Course(
            code="TEST 1001",
            normalized_code="TEST1001",
            name="Existing Course",
            credits=1,
        )
        db.session.add(existing)
        db.session.commit()
        existing_id = existing.id

        plan = build_import_plan(snapshot)
        assert plan.course_rows_to_insert == 1
        assert plan.course_rows_to_update == 1

        apply_snapshot(snapshot)

        resolved = Course.query.filter_by(normalized_code="TEST1001").one()
        assert resolved.id == existing_id
        assert resolved.name == "Test Course"
        assert Course.query.filter_by(code="TEST1001").count() == 0
        assert CourseOffering.query.filter_by(
            course_id=existing_id,
            semester_id="2540",
        ).one()


def test_apply_offerings_reuses_course_row_with_non_space_whitespace(app, tmp_path):
    snapshot = load_offerings_file(write_payload(tmp_path, payload()))

    with app.app_context():
        existing = Course(
            code="TEST\t1001",
            normalized_code=None,
            name="Legacy Whitespace Course",
            credits=1,
        )
        db.session.add(existing)
        db.session.commit()
        existing_id = existing.id

        plan = build_import_plan(snapshot)
        assert plan.course_rows_to_insert == 1
        assert plan.course_rows_to_update == 1

        apply_snapshot(snapshot)

        resolved = Course.query.filter_by(normalized_code="TEST1001").one()
        assert resolved.id == existing_id
        assert resolved.code == "TEST\t1001"
        assert Course.query.filter_by(code="TEST1001").count() == 0
        assert CourseOffering.query.filter_by(
            course_id=existing_id,
            semester_id="2540",
        ).one()


def test_apply_offerings_rejects_ambiguous_normalized_course_rows(app, tmp_path):
    snapshot = load_offerings_file(write_payload(tmp_path, payload()))

    with app.app_context():
        canonical = Course(
            code="TEST 1001",
            normalized_code="TEST1001",
            name="Canonical",
            credits=3,
        )
        duplicate = Course(
            code="TEST1001",
            normalized_code=None,
            name="Legacy Duplicate",
            credits=3,
        )
        db.session.add_all([canonical, duplicate])
        db.session.flush()
        offering = CourseOffering(
            course_id=duplicate.id,
            semester_id="2540",
            offering_code="TEST1001",
            title_snapshot="Legacy Duplicate",
            credits_snapshot=3,
            source="test",
            status="offered",
        )
        db.session.add(offering)
        db.session.flush()
        db.session.add(CourseSection(
            offering_id=offering.id,
            source_section_id="TEST1001-L01",
            name="L01",
            section_type="L",
            bundle=1,
            layer=0,
            quota=30,
            is_main=True,
        ))
        db.session.commit()

        with pytest.raises(OfferingValidationError, match="ambiguous existing course rows"):
            apply_snapshot(snapshot)

        assert Course.query.filter(
            Course.code.in_(["TEST 1001", "TEST1001"])
        ).count() == 2
        assert CourseOffering.query.filter_by(semester_id="2540").count() == 1


def test_apply_offerings_rejects_inconsistent_normalized_course_identity(app, tmp_path):
    snapshot = load_offerings_file(write_payload(tmp_path, payload()))

    with app.app_context():
        db.session.add(Course(
            code="WRNG1001",
            normalized_code="TEST1001",
            name="Inconsistent",
            credits=3,
        ))
        db.session.commit()

        with pytest.raises(OfferingValidationError, match="normalization is inconsistent"):
            apply_snapshot(snapshot)

        assert CourseOffering.query.filter_by(semester_id="2540").count() == 0


def test_apply_offerings_formats_suffixed_course_display_code(app, tmp_path):
    data = payload()
    data["courses"] = [{
        "course_code": "UCUG1052A",
        "course_title": "Academic English for University Studies",
        "course_desc": "",
        "credit": 3,
        "subject": "UCUG",
        "catalog_number": "1052A",
        "pg_course": False,
        "sections": [],
    }]
    snapshot = load_offerings_file(write_payload(tmp_path, data))

    with app.app_context():
        apply_snapshot(snapshot)

        course = Course.query.filter_by(code="UCUG1052A").one()
        assert course.normalized_code == "UCUG1052A"
        assert course.display_code == "UCUG 1052A"


def test_apply_offerings_preserves_existing_course_rules_when_snapshot_rules_are_empty(app, tmp_path):
    snapshot = load_offerings_file(write_payload(tmp_path, {
        "semester_id": "2530",
        "courses": [
            {
                "course_code": "RULE1504",
                "course_title": "Honors General Physics II",
                "course_desc": "Offering description.",
                "credit": 3,
                "subject": "RULE",
                "catalog_number": "1504",
                "sections": [],
            }
        ],
    }))

    with app.app_context():
        db.session.add(Course(
            code="RULE1504",
            name="Honors General Physics II",
            credits=3,
            pre_requirement="(UFUG 1501 or UFUG 1503) AND (UFUG 1102 or UFUG 1105)",
            exclusion="UFUG 1502",
        ))
        db.session.commit()

        apply_snapshot(snapshot)

        course = Course.query.filter_by(code="RULE1504").one()

    assert course.pre_requirement == "(UFUG 1501 or UFUG 1503) AND (UFUG 1102 or UFUG 1105)"
    assert course.co_requirement is None
    assert course.exclusion == "UFUG 1502"


def test_apply_offerings_preserves_user_section_choices(app, tmp_path):
    first_snapshot = load_offerings_file(write_payload(tmp_path, payload()))

    with app.app_context():
        apply_snapshot(first_snapshot)
        course = Course.query.filter_by(code="TEST1001").one()
        offering = CourseOffering.query.filter_by(
            course_id=course.id,
            semester_id="2540",
        ).one()
        section = CourseSection.query.filter_by(
            offering_id=offering.id,
            source_section_id="TEST1001-L01",
        ).one()
        original_section_id = section.id

        role = UserRole.query.filter_by(name="user").first() or UserRole(name="user")
        db.session.add(role)
        db.session.flush()
        user = User(
            username="scheduler_choice_user",
            email="scheduler_choice@hkust-gz.edu.cn",
            email_verified=True,
            role_id=role.id,
        )
        user.set_password("password123")
        db.session.add(user)
        db.session.flush()
        db.session.add(UserOfferingCart(
            user_id=user.id,
            offering_id=offering.id,
            enabled=True,
        ))
        db.session.add(UserSectionSelection(
            user_id=user.id,
            offering_id=offering.id,
            section_id=section.id,
            enabled=False,
            source="cart",
        ))
        db.session.commit()

        updated_payload = payload()
        updated_payload["courses"][0]["sections"][0]["quota"] = 60
        updated_payload["courses"][0]["sections"][0]["lectures"][0]["room"] = "Room 202"
        second_snapshot = load_offerings_file(write_payload(tmp_path, updated_payload))
        apply_snapshot(second_snapshot, allow_destructive_replacement=True)
        db.session.expire_all()

        refreshed_section = CourseSection.query.filter_by(
            offering_id=offering.id,
            source_section_id="TEST1001-L01",
        ).one()
        selection = UserSectionSelection.query.filter_by(
            user_id=user.id,
            offering_id=offering.id,
            section_id=refreshed_section.id,
        ).one()

        assert refreshed_section.id == original_section_id
        assert refreshed_section.quota == 60
        assert refreshed_section.meetings.one().room == "Room 202"
        assert selection.enabled is False


def test_apply_offerings_backfills_new_section_choices_from_bundle_layer(app, tmp_path):
    first_snapshot = load_offerings_file(write_payload(tmp_path, payload()))

    with app.app_context():
        apply_snapshot(first_snapshot)
        course = Course.query.filter_by(code="TEST1001").one()
        offering = CourseOffering.query.filter_by(
            course_id=course.id,
            semester_id="2540",
        ).one()
        original = CourseSection.query.filter_by(
            offering_id=offering.id,
            source_section_id="TEST1001-L01",
        ).one()
        role = UserRole.query.filter_by(name="user").first() or UserRole(name="user")
        db.session.add(role)
        db.session.flush()
        user = User(
            username="scheduler_new_section_user",
            email="scheduler_new_section@hkust-gz.edu.cn",
            email_verified=True,
            role_id=role.id,
        )
        user.set_password("password123")
        db.session.add(user)
        db.session.flush()
        db.session.add(UserOfferingCart(
            user_id=user.id,
            offering_id=offering.id,
            enabled=False,
        ))
        db.session.add(UserSectionSelection(
            user_id=user.id,
            offering_id=offering.id,
            section_id=original.id,
            enabled=False,
            source="cart",
        ))
        db.session.commit()

        updated_payload = payload()
        updated_payload["courses"][0]["sections"].extend([
            {
                "semester_id": "2540",
                "section_id": "TEST1001-L02",
                "course_code": "TEST1001",
                "section_type": "L",
                "name": "L02",
                "bundle": 1,
                "layer": 0,
                "quota": 50,
                "is_main": True,
                "lectures": [],
            },
            {
                "semester_id": "2540",
                "section_id": "TEST1001-T01",
                "course_code": "TEST1001",
                "section_type": "T",
                "name": "T01",
                "bundle": 1,
                "layer": 1,
                "quota": 25,
                "is_main": False,
                "lectures": [],
            },
        ])
        apply_snapshot(load_offerings_file(write_payload(tmp_path, updated_payload)))

        selections = {
            selection.section.source_section_id: selection.enabled
            for selection in UserSectionSelection.query.filter_by(
                user_id=user.id,
                offering_id=offering.id,
            ).all()
        }

        assert selections == {
            "TEST1001-L01": False,
            "TEST1001-L02": False,
            "TEST1001-T01": True,
        }


def test_apply_offerings_backfills_missing_existing_section_choices(app, tmp_path):
    snapshot = load_offerings_file(write_payload(tmp_path, payload()))

    with app.app_context():
        apply_snapshot(snapshot)
        course = Course.query.filter_by(code="TEST1001").one()
        offering = CourseOffering.query.filter_by(
            course_id=course.id,
            semester_id="2540",
        ).one()
        role = UserRole.query.filter_by(name="user").first() or UserRole(name="user")
        db.session.add(role)
        db.session.flush()
        user = User(
            username="scheduler_missing_selection_user",
            email="scheduler_missing_selection@hkust-gz.edu.cn",
            email_verified=True,
            role_id=role.id,
        )
        user.set_password("password123")
        db.session.add(user)
        db.session.flush()
        db.session.add(UserOfferingCart(
            user_id=user.id,
            offering_id=offering.id,
            enabled=False,
        ))
        db.session.commit()

        apply_snapshot(snapshot)

        selections = UserSectionSelection.query.filter_by(
            user_id=user.id,
            offering_id=offering.id,
        ).all()
        assert len(selections) == 1
        assert selections[0].enabled is True


def test_deploy_update_dry_run_does_not_write_database(app, tmp_path):
    path = write_payload(tmp_path, payload())
    digest = file_sha256(path)
    expected_counts = snapshot_counts(load_offerings_file(path))

    with app.app_context():
        result = run_deploy_scheduler_offering_update(
            mode="dry-run",
            file_path=path,
            expected_semester_id="2540",
            expected_sha256=digest,
            expected_counts=expected_counts,
        )

        assert result.status == "dry-run"
        assert result.plan.courses == 2
        assert SchedulerSection.query.filter_by(semester_id="2540").count() == 0
        assert SchedulerLecture.query.filter_by(semester_id="2540").count() == 0
        assert CourseOffering.query.filter_by(semester_id="2540").count() == 0


def test_deploy_update_blocks_unreviewed_or_mismatched_counts(app, tmp_path):
    path = write_payload(tmp_path, payload())
    digest = file_sha256(path)

    with app.app_context():
        unreviewed = run_deploy_scheduler_offering_update(
            mode="apply",
            file_path=path,
            expected_semester_id="2540",
            expected_sha256=digest,
        )
        mismatched = run_deploy_scheduler_offering_update(
            mode="apply",
            file_path=path,
            expected_semester_id="2540",
            expected_sha256=digest,
            expected_counts=SnapshotExpectations(3, 2, 2, 2),
        )

        assert unreviewed.status == "blocked"
        assert "reviewed snapshot counts are required" in unreviewed.message
        assert mismatched.status == "blocked"
        assert "does not match independently reviewed counts" in mismatched.message
        assert CourseOffering.query.filter_by(semester_id="2540").count() == 0
        assert SchedulerSection.query.filter_by(semester_id="2540").count() == 0


def test_deploy_update_returns_blocked_for_destructive_omissions(app, tmp_path):
    full_snapshot = load_offerings_file(write_payload(tmp_path, payload()))

    with app.app_context():
        apply_snapshot(full_snapshot)

        partial_payload = payload()
        partial_payload["courses"] = [partial_payload["courses"][1]]
        partial_path = write_payload(tmp_path, partial_payload)
        partial_snapshot = load_offerings_file(partial_path)
        result = run_deploy_scheduler_offering_update(
            mode="apply",
            file_path=partial_path,
            expected_semester_id="2540",
            expected_sha256=file_sha256(partial_path),
            expected_counts=snapshot_counts(partial_snapshot),
        )

        assert result.status == "blocked"
        assert "refusing destructive replacement" in result.message
        assert result.plan.omitted_offering_codes == ["TEST1001"]
        assert CourseOffering.query.filter_by(semester_id="2540", status="offered").count() == 1
        assert SchedulerSection.query.filter_by(semester_id="2540").count() == 1
        assert SchedulerLecture.query.filter_by(semester_id="2540").count() == 1


def test_deploy_update_apply_is_guarded_by_hash_and_runs_once(app, tmp_path):
    path = write_payload(tmp_path, payload())
    digest = file_sha256(path)
    expected_counts = snapshot_counts(load_offerings_file(path))

    with app.app_context():
        mismatch = run_deploy_scheduler_offering_update(
            mode="apply",
            file_path=path,
            expected_semester_id="2540",
            expected_sha256="0" * 64,
            expected_counts=expected_counts,
        )
        assert mismatch.status == "blocked"
        assert SchedulerSection.query.filter_by(semester_id="2540").count() == 0

        first = run_deploy_scheduler_offering_update(
            mode="apply",
            file_path=path,
            expected_semester_id="2540",
            expected_sha256=digest,
            expected_counts=expected_counts,
        )
        second = run_deploy_scheduler_offering_update(
            mode="apply",
            file_path=path,
            expected_semester_id="2540",
            expected_sha256=digest,
            expected_counts=expected_counts,
        )

        assert first.status == "applied"
        assert first.plan.sections == 1
        assert second.status == "skipped"
        assert SchedulerSection.query.filter_by(semester_id="2540").count() == 1
        assert SchedulerLecture.query.filter_by(semester_id="2540").count() == 1
        assert CourseOffering.query.filter_by(semester_id="2540").count() == 1
        assert CourseSection.query.join(CourseOffering).filter(
            CourseOffering.semester_id == "2540"
        ).count() == 1
