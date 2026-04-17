import pytest

from app import create_app
from app.extensions import db
from app.models.course import Course
from app.models.user import User
from app.models.user_role import UserRole
from app.routes.post import SYSTEM_REVIEW_TAG, validate_and_get_tag
from app.utils.semester import format_offering_display_tag


@pytest.fixture
def client():
    app = create_app()
    app.config["TESTING"] = True
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    app.config["JWT_SECRET_KEY"] = "test-secret"

    with app.test_client() as client:
        with app.app_context():
            db.create_all()
        yield client


def _create_course(code="AIAA2205"):
    course = Course(
        code=code,
        name="Introduction to Artificial Intelligence",
        description="",
        credits=3,
    )
    db.session.add(course)
    db.session.commit()
    return course


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

    response = client.get(
        f"/courses/{course.id}/discussions",
        query_string={"offering": "25-26Spring"},
    )

    assert response.status_code == 200
    payload = response.get_json()
    returned_ids = {post["id"] for post in payload["posts"]}

    assert payload["total_count"] == 3
    assert current_post.id in returned_ids
    assert earlier_same_year.id in returned_ids
    assert previous_year.id in returned_ids
    assert future_post.id not in returned_ids
    assert review_post.id not in returned_ids
    assert other_course_post.id not in returned_ids


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

    response = client.get(
        f"/courses/{course.id}/discussions",
        query_string={"offering": "25-26Fall"},
    )

    assert response.status_code == 200
    payload = response.get_json()
    returned_ids = {post["id"] for post in payload["posts"]}

    assert visible_fall_post.id in returned_ids
    assert hidden_spring_post.id not in returned_ids


def test_course_discussions_reject_unknown_offering_for_course(client):
    with client.application.app_context():
        course = _create_course()
        course.create_semester_tag("2024fall")
        _create_post_with_tags(
            "legacy-fall-post",
            [course.code, format_offering_display_tag("2024", "fall")],
        )

    response = client.get(
        f"/courses/{course.id}/discussions",
        query_string={"offering": "25-26Spring"},
    )

    assert response.status_code == 400
