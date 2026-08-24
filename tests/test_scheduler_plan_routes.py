import pytest
from flask_jwt_extended import create_access_token
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles

from app import create_app
from app.config import Config
from app.extensions import db
from app.models.course import Course
from app.models.course_domain import (
    CourseMeeting,
    CourseOffering,
    CourseSection,
    UserOfferingCart,
    UserSectionSelection,
)
from app.models.scheduler_plan import SchedulerPlan
from app.models.user import User
from app.models.user_role import UserRole
from app.services.content_moderation_service import content_moderation


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
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(
        content_moderation,
        "moderate_post",
        lambda **_kwargs: {"is_safe": True, "reason": ""},
    )
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        role = UserRole.query.filter_by(name="user").first()
        if role is None:
            role = UserRole(name="user", description="Regular user")
            db.session.add(role)
            db.session.flush()
        owner = User(
            username="plan-owner",
            email="plan-owner@hkust-gz.edu.cn",
            email_verified=True,
            role_id=role.id,
        )
        viewer = User(
            username="plan-viewer",
            email="plan-viewer@hkust-gz.edu.cn",
            email_verified=True,
            role_id=role.id,
        )
        owner.set_password("password")
        viewer.set_password("password")
        db.session.add_all([owner, viewer])

        course = Course(
            code="TEST1001",
            normalized_code="TEST1001",
            display_code="TEST 1001",
            name="Plan Testing",
            canonical_title="Plan Testing",
            credits=3,
            subject="TEST",
        )
        db.session.add(course)
        db.session.flush()
        offering = CourseOffering(
            course_id=course.id,
            semester_id="2610",
            offering_code="TEST1001",
            title_snapshot="Plan Testing",
            credits_snapshot=3,
            source="test",
            status="offered",
        )
        db.session.add(offering)
        db.session.flush()
        section = CourseSection(
            offering_id=offering.id,
            source_section_id="TEST1001-L01",
            name="L01",
            section_type="L",
            bundle=1,
            layer=0,
            quota=30,
            enrol=12,
            avail=18,
            wait=0,
            is_main=True,
            status="active",
        )
        db.session.add(section)
        db.session.flush()
        db.session.add(CourseMeeting(
            section_id=section.id,
            day=1,
            start_time=900,
            end_time=1030,
            room="Room 101",
            instructor_text="Dr Test",
        ))
        db.session.commit()
        app.config["OWNER_ID"] = owner.id
        app.config["VIEWER_ID"] = viewer.id
        app.config["OFFERING_ID"] = offering.id
        app.config["SECTION_ID"] = section.id
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def auth(app):
    with app.app_context():
        owner = create_access_token(identity=str(app.config["OWNER_ID"]))
        viewer = create_access_token(identity=str(app.config["VIEWER_ID"]))
    return {
        "owner": {"Authorization": f"Bearer {owner}"},
        "viewer": {"Authorization": f"Bearer {viewer}"},
    }


def plan_payload(**overrides):
    payload = {
        "name": "Monday plan",
        "description": "A focused timetable",
        "semester_id": "2610",
        "visibility": "private",
        "banned_periods": [[False] * 8 for _ in range(7)],
        "courses": [{
            "course_code": "TEST1001",
            "selections": [{"bundle_id": 1, "layer": 0}],
        }],
    }
    payload.update(overrides)
    return payload


def create_plan(client, headers, **overrides):
    response = client.post("/scheduler/plans", headers=headers, json=plan_payload(**overrides))
    assert response.status_code == 201
    return response.get_json()


def add_course_with_meeting(app, *, code, start_time, end_time, date_ranges):
    with app.app_context():
        course = Course(
            code=code,
            normalized_code=code,
            display_code=code,
            name=f"{code} Testing",
            canonical_title=f"{code} Testing",
            credits=3,
            subject="TEST",
        )
        db.session.add(course)
        db.session.flush()
        offering = CourseOffering(
            course_id=course.id,
            semester_id="2610",
            offering_code=code,
            title_snapshot=f"{code} Testing",
            credits_snapshot=3,
            source="test",
            status="offered",
        )
        db.session.add(offering)
        db.session.flush()
        section = CourseSection(
            offering_id=offering.id,
            source_section_id=f"{code}-L01",
            name="L01",
            section_type="L",
            bundle=1,
            layer=0,
            quota=30,
            enrol=12,
            avail=18,
            wait=0,
            is_main=True,
            status="active",
        )
        db.session.add(section)
        db.session.flush()
        db.session.add(CourseMeeting(
            section_id=section.id,
            day=1,
            start_time=start_time,
            end_time=end_time,
            room="Room 202",
            instructor_text="Dr Second",
            date_ranges=date_ranges,
        ))
        db.session.commit()


def add_ctdl_module_course(app):
    with app.app_context():
        course = Course.query.filter_by(normalized_code="UCUG1000").first()
        if course is None:
            course = Course(code="UCUG1000", normalized_code="UCUG1000")
            db.session.add(course)
        course.display_code = "UCUG 1000"
        course.name = "Critical Thinking and Data Literacy"
        course.canonical_title = course.name
        course.credits = 3
        course.subject = "UCUG"
        course.klms_course = True
        db.session.flush()
        offering = CourseOffering.query.filter_by(
            course_id=course.id,
            semester_id="2610",
        ).first()
        if offering is None:
            offering = CourseOffering(course_id=course.id, semester_id="2610")
            db.session.add(offering)
        offering.offering_code = "UCUG1000"
        offering.title_snapshot = course.name
        offering.credits_snapshot = 3
        offering.source = "klms"
        offering.status = "offered"
        db.session.flush()
        for module_code, bundle, layer in (
            ("M01", 1, 0),
            ("M05", 1, 2),
            ("M06", 2, 2),
        ):
            db.session.add(CourseSection(
                offering_id=offering.id,
                source_section_id=f"{module_code}-L01",
                name="L01",
                section_type=module_code,
                bundle=bundle,
                layer=layer,
                quota=30,
                enrol=0,
                avail=30,
                wait=0,
                is_main=True,
                status="active",
                remarks=f"{module_code} title · KLMS module credit: 1",
            ))
        db.session.commit()


def test_plan_routes_require_authentication(client):
    assert client.post("/scheduler/plans", json=plan_payload()).status_code == 401
    assert client.get("/scheduler/plans/mine").status_code == 401
    assert client.delete("/scheduler/cart/2610").status_code == 401


def test_owner_can_create_list_and_read_exact_schedule(client, auth):
    plan = create_plan(client, auth["owner"])
    assert plan["name"] == "Monday plan"
    assert plan["visibility"] == "private"
    assert plan["availability"] == "current"
    assert plan["course_codes"] == ["TEST1001"]
    assert plan["selections"] == [{"courseIndex": 0, "bundleId": 1, "layer": 0}]
    assert "banned_periods" in plan

    listed = client.get("/scheduler/plans/mine", headers=auth["owner"]).get_json()
    assert [item["public_id"] for item in listed["plans"]] == [plan["public_id"]]
    read = client.get(f"/scheduler/plans/{plan['public_id']}", headers=auth["owner"])
    assert read.status_code == 200


def test_saved_ctdl_plan_allows_two_electives_from_the_same_teaching_layer(client, auth, app):
    add_ctdl_module_course(app)
    response = client.post(
        "/scheduler/plans",
        headers=auth["owner"],
        json=plan_payload(courses=[{
            "course_code": "UCUG1000",
            "selections": [
                {"bundle_id": 1, "layer": 0},
                {"bundle_id": 1, "layer": 2},
                {"bundle_id": 2, "layer": 2},
            ],
        }]),
    )

    assert response.status_code == 201
    data = response.get_json()
    assert data["selections"] == [
        {"courseIndex": 0, "bundleId": 1, "layer": 0},
        {"courseIndex": 0, "bundleId": 1, "layer": 2},
        {"courseIndex": 0, "bundleId": 2, "layer": 2},
    ]


def test_visibility_matrix_and_public_discovery(client, auth):
    private = create_plan(client, auth["owner"], name="Private")
    unlisted = create_plan(client, auth["owner"], name="Unlisted", visibility="unlisted")
    public = create_plan(client, auth["owner"], name="Public", visibility="public")

    assert client.get(f"/scheduler/plans/{private['public_id']}").status_code == 404
    assert client.get(f"/scheduler/plans/{unlisted['public_id']}").status_code == 200
    shared = client.get("/scheduler/plans/shared?semester_id=2610&course_code=TEST1001")
    assert shared.status_code == 200
    assert [item["public_id"] for item in shared.get_json()["plans"]] == [public["public_id"]]


def test_update_uses_optimistic_versioning_and_hides_private_constraints(client, auth):
    plan = create_plan(client, auth["owner"], visibility="public")
    response = client.patch(
        f"/scheduler/plans/{plan['public_id']}",
        headers=auth["owner"],
        json={"version": plan["version"], "name": "Revised plan"},
    )
    assert response.status_code == 200
    updated = response.get_json()
    assert updated["version"] == plan["version"] + 1
    stale = client.patch(
        f"/scheduler/plans/{plan['public_id']}",
        headers=auth["owner"],
        json={"version": plan["version"], "name": "Stale edit"},
    )
    assert stale.status_code == 409
    assert stale.get_json()["code"] == "version_conflict"

    public_view = client.get(f"/scheduler/plans/{plan['public_id']}").get_json()
    assert "banned_periods" not in public_view


def test_clone_is_an_independent_private_copy(client, auth):
    source = create_plan(client, auth["owner"], visibility="public")
    response = client.post(
        f"/scheduler/plans/{source['public_id']}/clone",
        headers=auth["viewer"],
        json={"name": "My copied plan"},
    )
    assert response.status_code == 201
    clone = response.get_json()
    assert clone["public_id"] != source["public_id"]
    assert clone["visibility"] == "private"
    assert clone["is_owner"] is True
    assert clone["name"] == "My copied plan"


def test_seat_counts_do_not_stale_plan_but_meeting_changes_do(client, app, auth):
    plan = create_plan(client, auth["owner"], visibility="public")
    with app.app_context():
        section = db.session.get(CourseSection, app.config["SECTION_ID"])
        section.enrol = 20
        section.avail = 10
        db.session.commit()
    assert client.get(f"/scheduler/plans/{plan['public_id']}").get_json()["availability"] == "current"

    with app.app_context():
        meeting = CourseMeeting.query.filter_by(section_id=app.config["SECTION_ID"]).one()
        meeting.room = "Room 202"
        db.session.commit()
    assert client.get(f"/scheduler/plans/{plan['public_id']}").get_json()["availability"] == "updated"


def test_unavailable_plan_cannot_partially_replace_workspace(client, app, auth):
    plan = create_plan(client, auth["owner"])
    with app.app_context():
        offering_id = app.config["OFFERING_ID"]
        section_id = app.config["SECTION_ID"]
        db.session.add(UserOfferingCart(
            user_id=app.config["OWNER_ID"], offering_id=offering_id, enabled=False
        ))
        db.session.add(UserSectionSelection(
            user_id=app.config["OWNER_ID"],
            offering_id=offering_id,
            section_id=section_id,
            enabled=True,
            source="cart",
        ))
        db.session.get(CourseSection, section_id).status = "cancelled"
        db.session.commit()

    response = client.post(f"/scheduler/plans/{plan['public_id']}/apply", headers=auth["owner"])
    assert response.status_code == 409
    assert response.get_json()["code"] == "plan_unavailable"
    with app.app_context():
        cart = UserOfferingCart.query.filter_by(user_id=app.config["OWNER_ID"]).one()
        assert cart.enabled is False


def test_apply_and_new_workspace_replace_then_clear_cart(client, app, auth):
    plan = create_plan(client, auth["owner"])
    applied = client.post(f"/scheduler/plans/{plan['public_id']}/apply", headers=auth["owner"])
    assert applied.status_code == 200
    with app.app_context():
        cart = UserOfferingCart.query.filter_by(user_id=app.config["OWNER_ID"]).one()
        selection = UserSectionSelection.query.filter_by(
            user_id=app.config["OWNER_ID"], enabled=True
        ).one()
        assert cart.enabled is True
        assert selection.section_id == app.config["SECTION_ID"]

    cleared = client.delete("/scheduler/cart/2610", headers=auth["owner"])
    assert cleared.status_code == 200
    assert cleared.get_json()["removed_courses"] == 1
    with app.app_context():
        assert UserOfferingCart.query.filter_by(user_id=app.config["OWNER_ID"]).count() == 0
        assert UserSectionSelection.query.filter_by(user_id=app.config["OWNER_ID"]).count() == 0


def test_apply_allows_same_section_meetings_with_changed_room_and_date(client, app, auth):
    with app.app_context():
        first = CourseMeeting.query.filter_by(section_id=app.config["SECTION_ID"]).one()
        first.date_ranges = [{"start_date": "2026-09-07", "end_date": "2026-09-13"}]
        db.session.add(CourseMeeting(
            section_id=app.config["SECTION_ID"],
            day=1,
            start_time=900,
            end_time=1030,
            room="Room 102",
            instructor_text="Dr Test",
            date_ranges=[{"start_date": "2026-09-14", "end_date": "2026-12-07"}],
        ))
        db.session.commit()

    plan = create_plan(client, auth["owner"])
    response = client.post(f"/scheduler/plans/{plan['public_id']}/apply", headers=auth["owner"])
    assert response.status_code == 200


def test_apply_allows_different_sections_with_disjoint_teaching_dates(client, app, auth):
    with app.app_context():
        first = CourseMeeting.query.filter_by(section_id=app.config["SECTION_ID"]).one()
        first.date_ranges = [{"start_date": "2026-09-07", "end_date": "2026-09-13"}]
        db.session.commit()
    add_course_with_meeting(
        app,
        code="TEST1002",
        start_time=900,
        end_time=1030,
        date_ranges=[{"start_date": "2026-09-14", "end_date": "2026-12-07"}],
    )
    plan = create_plan(client, auth["owner"], courses=[
        {"course_code": "TEST1001", "selections": [{"bundle_id": 1, "layer": 0}]},
        {"course_code": "TEST1002", "selections": [{"bundle_id": 1, "layer": 0}]},
    ])

    response = client.post(f"/scheduler/plans/{plan['public_id']}/apply", headers=auth["owner"])
    assert response.status_code == 200


@pytest.mark.parametrize(
    "second_ranges",
    [
        [{"start_date": "2026-09-13", "end_date": "2026-10-01"}],
        [],
    ],
)
def test_apply_rejects_overlapping_or_unbounded_teaching_dates(
    client, app, auth, second_ranges,
):
    with app.app_context():
        first = CourseMeeting.query.filter_by(section_id=app.config["SECTION_ID"]).one()
        first.date_ranges = [{"start_date": "2026-09-07", "end_date": "2026-09-13"}]
        db.session.commit()
    add_course_with_meeting(
        app,
        code="TEST1002",
        start_time=900,
        end_time=1030,
        date_ranges=second_ranges,
    )
    plan = create_plan(client, auth["owner"], courses=[
        {"course_code": "TEST1001", "selections": [{"bundle_id": 1, "layer": 0}]},
        {"course_code": "TEST1002", "selections": [{"bundle_id": 1, "layer": 0}]},
    ])

    response = client.post(f"/scheduler/plans/{plan['public_id']}/apply", headers=auth["owner"])
    assert response.status_code == 409
    assert response.get_json()["code"] == "updated_plan_conflict"


def test_applying_shared_plan_does_not_use_owner_private_blocked_periods(client, app, auth):
    blocked = [[False] * 8 for _ in range(7)]
    blocked[0][0] = True
    plan = create_plan(
        client,
        auth["owner"],
        visibility="public",
        banned_periods=blocked,
    )

    owner_apply = client.post(
        f"/scheduler/plans/{plan['public_id']}/apply",
        headers=auth["owner"],
    )
    assert owner_apply.status_code == 409
    assert owner_apply.get_json()["code"] == "updated_plan_conflict"

    viewer_apply = client.post(
        f"/scheduler/plans/{plan['public_id']}/apply",
        headers=auth["viewer"],
    )
    assert viewer_apply.status_code == 200
    with app.app_context():
        assert UserOfferingCart.query.filter_by(user_id=app.config["VIEWER_ID"]).count() == 1


def test_delete_is_soft_and_owner_only(client, app, auth):
    plan = create_plan(client, auth["owner"], visibility="public")
    assert client.delete(
        f"/scheduler/plans/{plan['public_id']}", headers=auth["viewer"]
    ).status_code == 404
    assert client.delete(
        f"/scheduler/plans/{plan['public_id']}", headers=auth["owner"]
    ).status_code == 204
    assert client.get(f"/scheduler/plans/{plan['public_id']}").status_code == 404
    with app.app_context():
        saved = SchedulerPlan.query.filter_by(public_id=plan["public_id"]).one()
        assert saved.is_deleted is True
        assert saved.deleted_at is not None


@pytest.mark.parametrize(
    "override,code",
    [
        ({"name": ""}, "name_required"),
        ({"visibility": "friends"}, "invalid_visibility"),
        ({"courses": []}, "courses_required"),
        ({"banned_periods": []}, "invalid_blocked_periods"),
    ],
)
def test_invalid_payloads_return_stable_codes(client, auth, override, code):
    response = client.post("/scheduler/plans", headers=auth["owner"], json=plan_payload(**override))
    assert response.status_code == 400
    assert response.get_json()["code"] == code
