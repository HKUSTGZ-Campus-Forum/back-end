import pytest

from app import create_app
from app.extensions import db
from app.models.post import serialize_post_tag
from app.models.tag import Tag, TagType
from app.models.user import User
from app.models.user_role import UserRole
from app.routes.post import (
    MAX_POST_TAG_COUNT,
    MAX_POST_TAG_LENGTH,
    SYSTEM_REVIEW_TAG,
    normalize_post_tags,
    validate_and_get_tag,
)


def test_normalize_post_tags_trims_collapses_spaces_and_dedupes():
    assert normalize_post_tags([
        " instant-discussion ",
        "study   group",
        "INSTANT-DISCUSSION",
        "",
        "   ",
    ]) == ["instant-discussion", "study group"]


def test_normalize_post_tags_rejects_too_many_tags():
    with pytest.raises(ValueError, match=f"at most {MAX_POST_TAG_COUNT} tags"):
        normalize_post_tags([f"tag-{i}" for i in range(MAX_POST_TAG_COUNT + 1)])


def test_normalize_post_tags_rejects_long_tag():
    with pytest.raises(ValueError, match=f"{MAX_POST_TAG_LENGTH} characters or fewer"):
        normalize_post_tags(["x" * (MAX_POST_TAG_LENGTH + 1)])


def test_serialize_post_tag_returns_frontend_friendly_fields():
    user_tag_type = TagType(name=TagType.USER)
    tag = Tag(id=7, name="instant-discussion", tag_type=user_tag_type)

    serialized = serialize_post_tag(tag)

    assert serialized["id"] == 7
    assert serialized["tag_id"] == 7
    assert serialized["name"] == "instant-discussion"
    assert serialized["tag_name"] == "instant-discussion"
    assert serialized["tag_type"] == TagType.USER
    assert serialized["isImportant"] is False
    assert serialized["tagcolor"] == "#3498db"


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


def test_validate_and_get_tag_treats_display_offering_tag_as_freeform_tag(client):
    with client.application.app_context():
        tag = validate_and_get_tag("25-26Fall")

        assert tag.name == "25-26Fall"
        assert tag.tag_type.name == TagType.USER


def test_validate_and_get_tag_does_not_enforce_legacy_course_semester_tag(client):
    with client.application.app_context():
        tag = validate_and_get_tag("AIAA2205-25Fall")

        assert tag.name == "AIAA2205-25Fall"
        assert tag.tag_type.name == TagType.USER


def test_get_posts_supports_all_tag_match_and_exclude_tags(client):
    with client.application.app_context():
        _create_post_with_tags(
            "review-post",
            ["AIAA2205", "25-26Fall", SYSTEM_REVIEW_TAG],
        )
        discussion_post = _create_post_with_tags(
            "discussion-post",
            ["AIAA2205", "25-26Fall"],
        )
        _create_post_with_tags(
            "partial-match-post",
            ["AIAA2205"],
        )

    response = client.get(
        "/posts",
        query_string={
            "tags": "AIAA2205,25-26Fall",
            "tag_match": "all",
            "exclude_tags": SYSTEM_REVIEW_TAG,
        },
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["total_count"] == 1
    assert payload["posts"][0]["id"] == discussion_post.id
