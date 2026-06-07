import pytest
from flask_jwt_extended import create_access_token
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles

from app import create_app
from app.config import Config
from app.extensions import db
from app.models.course import Course
from app.models.course_domain import CourseOffering, CoursePostOfferingTarget
from app.models.user import User
from app.models.user_role import UserRole
from app.routes.post import SYSTEM_REVIEW_TAG, validate_and_get_tag
from app.utils.semester import format_offering_display_tag


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
def client(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    for proxy_key in ["ALL_PROXY", "all_proxy", "HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"]:
        monkeypatch.delenv(proxy_key, raising=False)
    app = create_app(TestConfig)

    with app.test_client() as client:
        with app.app_context():
            db.create_all()
        yield client


def _create_course(code="AIAA2205"):
    existing = Course.query.filter_by(code=code).first()
    if existing:
        existing.name = "Introduction to Artificial Intelligence"
        existing.description = ""
        existing.credits = 3
        db.session.commit()
        return existing

    course = Course(
        code=code,
        name="Introduction to Artificial Intelligence",
        description="",
        credits=3,
    )
    db.session.add(course)
    db.session.commit()
    return course


def _create_offering(course, semester_id="2530"):
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
    db.session.commit()
    return offering


def _auth_headers(username="review-user"):
    role = UserRole.query.filter_by(name=UserRole.USER).first()
    if not role:
        role = UserRole(name=UserRole.USER)
        db.session.add(role)
        db.session.flush()
    user = User(username=username, email=f"{username}@example.com", role_id=role.id, email_verified=True)
    user.set_password("password")
    db.session.add(user)
    db.session.commit()
    token = create_access_token(identity=str(user.id))
    return {"Authorization": f"Bearer {token}"}


def _create_post_with_tags(title, tag_names):
    role = UserRole.query.filter_by(name=UserRole.USER).first()
    if not role:
        role = UserRole(name=UserRole.USER)
        db.session.add(role)
        db.session.flush()

    user = User(username=f"user-{title}", email=f"{title}@example.com", role_id=role.id)
    user.set_password("password")
    db.session.add(user)
    db.session.flush()

    from app.models.post import Post

    post = Post(user_id=user.id, title=title, content=f"{title} content")
    db.session.add(post)
    db.session.flush()

    for tag_name in tag_names:
        post.tags.append(validate_and_get_tag(tag_name))

    db.session.commit()
    return post


def test_course_discussions_include_current_and_previous_offerings_only(client):
    with client.application.app_context():
        course = _create_course()
        other_course = _create_course("COMP1000")

        for semester in [
            "2024fall",
            "2024spring",
            "2024summer",
            "2025fall",
            "2025spring",
            "2025summer",
        ]:
            course.create_semester_tag(semester)
        other_course.create_semester_tag("2025spring")

        current_post = _create_post_with_tags(
            "current-spring-discussion",
            [course.code, format_offering_display_tag("2025", "spring")],
        )
        earlier_same_year = _create_post_with_tags(
            "earlier-fall-discussion",
            [course.code, format_offering_display_tag("2025", "fall")],
        )
        previous_year = _create_post_with_tags(
            "previous-summer-discussion",
            [course.code, format_offering_display_tag("2024", "summer")],
        )
        future_post = _create_post_with_tags(
            "future-summer-discussion",
            [course.code, format_offering_display_tag("2025", "summer")],
        )
        review_post = _create_post_with_tags(
            "review-post",
            [course.code, format_offering_display_tag("2025", "spring"), SYSTEM_REVIEW_TAG],
        )
        other_course_post = _create_post_with_tags(
            "other-course-discussion",
            [other_course.code, format_offering_display_tag("2025", "spring")],
        )
        course_id = course.id
        current_post_id = current_post.id
        earlier_same_year_id = earlier_same_year.id
        previous_year_id = previous_year.id
        future_post_id = future_post.id
        review_post_id = review_post.id
        other_course_post_id = other_course_post.id

    response = client.get(
        f"/courses/{course_id}/discussions",
        query_string={"offering": "25-26Spring"},
    )

    assert response.status_code == 200
    payload = response.get_json()
    returned_ids = {post["id"] for post in payload["posts"]}

    assert payload["total_count"] == 3
    assert current_post_id in returned_ids
    assert earlier_same_year_id in returned_ids
    assert previous_year_id in returned_ids
    assert future_post_id not in returned_ids
    assert review_post_id not in returned_ids
    assert other_course_post_id not in returned_ids


def test_course_discussions_for_fall_offering_excludes_newer_same_year_posts(client):
    with client.application.app_context():
        course = _create_course()

        for semester in ["2024fall", "2025fall", "2025spring"]:
            course.create_semester_tag(semester)

        visible_fall_post = _create_post_with_tags(
            "visible-fall-post",
            [course.code, format_offering_display_tag("2025", "fall")],
        )
        hidden_spring_post = _create_post_with_tags(
            "hidden-spring-post",
            [course.code, format_offering_display_tag("2025", "spring")],
        )
        course_id = course.id
        visible_fall_post_id = visible_fall_post.id
        hidden_spring_post_id = hidden_spring_post.id

    response = client.get(
        f"/courses/{course_id}/discussions",
        query_string={"offering": "25-26Fall"},
    )

    assert response.status_code == 200
    payload = response.get_json()
    returned_ids = {post["id"] for post in payload["posts"]}

    assert visible_fall_post_id in returned_ids
    assert hidden_spring_post_id not in returned_ids


def test_course_discussions_reject_unknown_offering_for_course(client):
    with client.application.app_context():
        course = _create_course()
        course.create_semester_tag("2024fall")
        _create_post_with_tags(
            "legacy-fall-post",
            [course.code, format_offering_display_tag("2024", "fall")],
        )
        course_id = course.id

    response = client.get(
        f"/courses/{course_id}/discussions",
        query_string={"offering": "25-26Spring"},
    )

    assert response.status_code == 400


def test_create_review_post_writes_offering_target(client):
    with client.application.app_context():
        course = _create_course()
        offering = _create_offering(course, "2530")
        headers = _auth_headers("review-target-user")
        course_code = course.code
        offering_id = offering.id

    response = client.post(
        "/posts",
        json={
            "title": "AIAA2205 review",
            "content": "Useful class.",
            "tags": [course_code, "25-26Spring", SYSTEM_REVIEW_TAG],
        },
        headers=headers,
    )

    assert response.status_code == 201
    post_id = response.get_json()["id"]
    with client.application.app_context():
        target = CoursePostOfferingTarget.query.filter_by(post_id=post_id).one()
        assert target.course_offering_id == offering_id


def test_create_review_post_rejects_unresolved_offering_target(client):
    with client.application.app_context():
        course = _create_course()
        headers = _auth_headers("review-target-missing-user")
        course_code = course.code

    response = client.post(
        "/posts",
        json={
            "title": "AIAA2205 review without offering",
            "content": "Useful class.",
            "tags": [course_code, "25-26Spring", SYSTEM_REVIEW_TAG],
        },
        headers=headers,
    )

    assert response.status_code == 400
    assert response.get_json()["error"] == "Review offering target could not be resolved"
    assert response.get_json()["code"] == "course_offering_not_resolved"
