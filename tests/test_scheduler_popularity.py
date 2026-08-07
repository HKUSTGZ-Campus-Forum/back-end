from datetime import datetime, timedelta, timezone

import pytest
from flask_jwt_extended import create_access_token
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles

from app import create_app
from app.config import Config
from app.extensions import db
from app.models.course import Course
from app.models.course_domain import (
    CourseOffering,
    CourseSection,
    UserOfferingCart,
    UserSectionSelection,
)
from app.models.scheduler_popularity import SchedulerPopularityEvent
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
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


def create_user(
    username,
    email,
    *,
    verified=True,
    deleted=False,
    created_at=None,
):
    role = UserRole.query.filter_by(name=UserRole.USER).first()
    if role is None:
        role = UserRole(name=UserRole.USER, description="Regular user")
        db.session.add(role)
        db.session.flush()
    user = User(
        username=username,
        email=email,
        email_verified=verified,
        is_deleted=deleted,
        role_id=role.id,
        created_at=created_at or datetime.now(timezone.utc),
    )
    user.set_password("password123")
    db.session.add(user)
    db.session.flush()
    return user


def headers_for(user):
    token = create_access_token(identity=str(user.id))
    return {"Authorization": f"Bearer {token}"}


def create_offering(code="POP1001", semester="2530", *, status="offered"):
    course = Course(
        code=code,
        normalized_code=code.replace(" ", "").upper(),
        name=f"Popularity {code}",
        credits=3,
        subject="TEST",
    )
    db.session.add(course)
    db.session.flush()
    offering = CourseOffering(
        course_id=course.id,
        semester_id=semester,
        offering_code=code,
        title_snapshot=course.name,
        credits_snapshot=3,
        source="test",
        status=status,
    )
    db.session.add(offering)
    db.session.flush()
    sections = [
        CourseSection(
            offering_id=offering.id,
            source_section_id=f"{code}-L01",
            name="L01",
            section_type="L",
            bundle=1,
            layer=0,
            quota=30,
            is_main=True,
        ),
        CourseSection(
            offering_id=offering.id,
            source_section_id=f"{code}-T01",
            name="T01",
            section_type="T",
            bundle=1,
            layer=1,
            quota=15,
            is_main=False,
        ),
    ]
    db.session.add_all(sections)
    db.session.flush()
    return course, offering, sections


def add_cart(user, offering, sections, *, enabled=False, selected=(True, True)):
    db.session.add(UserOfferingCart(
        user_id=user.id,
        offering_id=offering.id,
        enabled=enabled,
    ))
    for section, selection_enabled in zip(sections, selected):
        db.session.add(UserSectionSelection(
            user_id=user.id,
            offering_id=offering.id,
            section_id=section.id,
            enabled=selection_enabled,
            source="cart",
        ))


def test_popularity_requires_authenticated_verified_canonical_institutional_viewer(client, app):
    assert client.get("/scheduler/popularity/2530?course_codes=POP1001").status_code == 401

    with app.app_context():
        unverified = create_user("unverified", "unverified@hkust-gz.edu.cn", verified=False)
        external = create_user("external", "external@example.com")
        oldest = create_user(
            "oldest",
            "duplicate@hkust-gz.edu.cn",
            created_at=datetime.now(timezone.utc) - timedelta(days=1),
        )
        duplicate = create_user("duplicate", " DUPLICATE@HKUST-GZ.EDU.CN ")
        db.session.commit()
        unverified_headers = headers_for(unverified)
        external_headers = headers_for(external)
        oldest_headers = headers_for(oldest)
        duplicate_headers = headers_for(duplicate)

    assert client.get("/scheduler/popularity/2530", headers=unverified_headers).status_code == 403
    assert client.get("/scheduler/popularity/2530", headers=external_headers).status_code == 403
    assert client.get("/scheduler/popularity/2530", headers=duplicate_headers).status_code == 403
    assert client.get("/scheduler/popularity/2530", headers=oldest_headers).status_code == 200


def test_popularity_is_cart_scoped_exact_anonymous_and_excludes_ineligible_duplicates(client, app):
    with app.app_context():
        _, offering, sections = create_offering()
        _, hidden_offering, hidden_sections = create_offering("POP2001")
        viewer = create_user("viewer", "viewer@hkust-gz.edu.cn")
        looking = create_user("looking", "looking@connect.hkust-gz.edu.cn")
        scheduling = create_user("scheduling", "scheduling@hkust-gz.edu.cn")
        unverified = create_user("unverified_count", "uv@hkust-gz.edu.cn", verified=False)
        external = create_user("external_count", "outside@example.com")
        deleted = create_user("deleted_count", "deleted@hkust-gz.edu.cn", deleted=True)
        canonical = create_user(
            "canonical_count",
            "same@hkust-gz.edu.cn",
            created_at=datetime.now(timezone.utc) - timedelta(days=2),
        )
        duplicate = create_user("duplicate_count", " SAME@HKUST-GZ.EDU.CN ")
        create_user(
            "canonical_without_cart",
            "no_cart@hkust-gz.edu.cn",
            created_at=datetime.now(timezone.utc) - timedelta(days=3),
        )
        duplicate_with_cart = create_user(
            "duplicate_with_cart",
            " NO_CART@HKUST-GZ.EDU.CN ",
        )

        add_cart(viewer, offering, sections, selected=(False, False))
        add_cart(looking, offering, sections, enabled=False, selected=(True, False))
        add_cart(scheduling, offering, sections, enabled=True, selected=(True, True))
        add_cart(unverified, offering, sections)
        add_cart(external, offering, sections)
        add_cart(deleted, offering, sections)
        add_cart(canonical, offering, sections, enabled=False, selected=(True, True))
        add_cart(duplicate, offering, sections, enabled=True, selected=(True, True))
        add_cart(duplicate_with_cart, offering, sections, enabled=True, selected=(True, True))
        add_cart(looking, hidden_offering, hidden_sections)
        db.session.commit()
        viewer_headers = headers_for(viewer)

    response = client.get(
        "/scheduler/popularity/2530?course_codes=pop%201001,POP2001,POP1001",
        headers=viewer_headers,
    )

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.headers["Vary"] == "Authorization"
    data = response.get_json()
    assert set(data) == {"semester_id", "generated_at", "courses"}
    assert data["semester_id"] == "2530"
    assert len(data["courses"]) == 1
    course = data["courses"][0]
    assert set(course) == {"course_code", "looking_count", "scheduling_count", "sections"}
    assert course["course_code"] == "POP1001"
    # viewer + looking + canonical; the newer duplicate and ineligible accounts do not count.
    assert course["looking_count"] == 3
    assert course["scheduling_count"] == 1
    assert course["sections"] == [
        {"section_id": "POP1001-L01", "looking_count": 2, "scheduling_count": 1},
        {"section_id": "POP1001-T01", "looking_count": 1, "scheduling_count": 1},
    ]
    serialized = str(data).lower()
    for private_key in ("user_id", "username", "email", "event", "offering_id"):
        assert private_key not in serialized


def test_popularity_rejects_cross_offering_selection_and_initializes_zero_sections(client, app):
    with app.app_context():
        _, offering, sections = create_offering()
        _, other_offering, other_sections = create_offering("POP3001")
        viewer = create_user("viewer_cross", "viewer_cross@hkust-gz.edu.cn")
        contributor = create_user("contributor_cross", "contributor_cross@hkust-gz.edu.cn")
        add_cart(viewer, offering, sections, selected=(False, False))
        db.session.add(UserOfferingCart(
            user_id=contributor.id,
            offering_id=offering.id,
            enabled=False,
        ))
        # The section FK is valid, but its offering deliberately disagrees with the selection.
        db.session.add(UserSectionSelection(
            user_id=contributor.id,
            offering_id=offering.id,
            section_id=other_sections[0].id,
            enabled=True,
            source="cart",
        ))
        db.session.commit()
        viewer_headers = headers_for(viewer)

    course = client.get(
        "/scheduler/popularity/2530?course_codes=POP1001",
        headers=viewer_headers,
    ).get_json()["courses"][0]
    assert course["looking_count"] == 2
    assert course["sections"] == [
        {"section_id": "POP1001-L01", "looking_count": 0, "scheduling_count": 0},
        {"section_id": "POP1001-T01", "looking_count": 0, "scheduling_count": 0},
    ]


def test_popularity_empty_filter_limit_and_archived_cart_scope(client, app):
    with app.app_context():
        _, offering, sections = create_offering(status="archived")
        viewer = create_user("viewer_limits", "viewer_limits@hkust-gz.edu.cn")
        add_cart(viewer, offering, sections)
        db.session.commit()
        viewer_headers = headers_for(viewer)

    empty = client.get("/scheduler/popularity/2530", headers=viewer_headers)
    assert empty.status_code == 200
    assert empty.get_json()["courses"] == []

    archived = client.get(
        "/scheduler/popularity/2530?course_codes=POP1001",
        headers=viewer_headers,
    )
    assert archived.status_code == 200
    assert archived.get_json()["courses"] == []

    too_many = ",".join(f"TEST{i:04d}" for i in range(31))
    limited = client.get(
        f"/scheduler/popularity/2530?course_codes={too_many}",
        headers=viewer_headers,
    )
    assert limited.status_code == 400


def test_cart_mutations_log_only_actual_anonymous_transitions(client, app):
    with app.app_context():
        create_offering()
        user = create_user("event_user", "event_user@hkust-gz.edu.cn")
        db.session.commit()
        user_headers = headers_for(user)

    added = client.post(
        "/scheduler/cart/2530/add",
        json={"course_code": "POP1001"},
        headers=user_headers,
    )
    assert added.status_code == 200
    with app.app_context():
        assert SchedulerPopularityEvent.query.filter_by(reason="cart_added").count() == 3
        assert "user_id" not in SchedulerPopularityEvent.__table__.columns

    no_op = client.put(
        "/scheduler/cart/2530/course/POP1001/toggle",
        json={"enabled": False},
        headers=user_headers,
    )
    assert no_op.status_code == 200
    with app.app_context():
        assert SchedulerPopularityEvent.query.count() == 3

    enabled = client.put(
        "/scheduler/cart/2530/course/POP1001/toggle",
        json={"enabled": True},
        headers=user_headers,
    )
    assert enabled.status_code == 200
    with app.app_context():
        toggles = SchedulerPopularityEvent.query.filter_by(reason="course_toggled").all()
        assert len(toggles) == 3
        assert {(event.from_state, event.to_state) for event in toggles} == {
            ("looking", "scheduling")
        }

    disabled_bundle = client.put(
        "/scheduler/cart/2530/bundle/POP1001/1/1/toggle",
        json={"enabled": False},
        headers=user_headers,
    )
    assert disabled_bundle.status_code == 200
    with app.app_context():
        bundle_events = SchedulerPopularityEvent.query.filter_by(reason="bundle_toggled").all()
        assert len(bundle_events) == 1
        assert (bundle_events[0].from_state, bundle_events[0].to_state) == ("scheduling", None)

    removed = client.delete(
        "/scheduler/cart/2530/remove/POP1001",
        headers=user_headers,
    )
    assert removed.status_code == 200
    with app.app_context():
        removal_events = SchedulerPopularityEvent.query.filter_by(reason="cart_removed").all()
        assert len(removal_events) == 2
        assert sum(event.section_id is None for event in removal_events) == 1


@pytest.mark.parametrize("route", [
    "/scheduler/cart/2530/course/POP1001/toggle",
    "/scheduler/cart/2530/bundle/POP1001/1/0/toggle",
    "/scheduler/cart/2530/layer/POP1001/0/toggle",
])
def test_cart_toggles_require_boolean_enabled(client, app, route):
    with app.app_context():
        _, offering, sections = create_offering()
        user = create_user(f"bool_{route.split('/')[-2]}", f"bool_{route.split('/')[-2]}@hkust-gz.edu.cn")
        add_cart(user, offering, sections)
        db.session.commit()
        user_headers = headers_for(user)

    response = client.put(route, json={"enabled": "false"}, headers=user_headers)
    assert response.status_code == 400
    assert response.get_json() == {"error": "enabled must be a boolean"}
    with app.app_context():
        assert SchedulerPopularityEvent.query.count() == 0


def test_ineligible_and_non_offered_mutations_do_not_log(client, app):
    with app.app_context():
        create_offering("ARCH1001", status="archived")
        create_offering("POP1001")
        user = create_user("unverified_event", "unverified_event@hkust-gz.edu.cn", verified=False)
        create_user(
            "canonical_event",
            "duplicate_event@hkust-gz.edu.cn",
            created_at=datetime.now(timezone.utc) - timedelta(days=1),
        )
        duplicate = create_user("duplicate_event", " DUPLICATE_EVENT@HKUST-GZ.EDU.CN ")
        db.session.commit()
        user_headers = headers_for(user)
        duplicate_headers = headers_for(duplicate)

    archived = client.post(
        "/scheduler/cart/2530/add",
        json={"course_code": "ARCH1001"},
        headers=user_headers,
    )
    assert archived.status_code == 422
    added = client.post(
        "/scheduler/cart/2530/add",
        json={"course_code": "POP1001"},
        headers=user_headers,
    )
    assert added.status_code == 200
    duplicate_added = client.post(
        "/scheduler/cart/2530/add",
        json={"course_code": "POP1001"},
        headers=duplicate_headers,
    )
    assert duplicate_added.status_code == 200
    with app.app_context():
        assert SchedulerPopularityEvent.query.count() == 0
