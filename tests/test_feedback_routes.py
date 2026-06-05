import os

import pytest
from flask_jwt_extended import create_access_token
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles

from app import create_app
from app.config import Config
from app.extensions import db
from app.models.feedback import Feedback
from app.models.feedback_version import FeedbackVersion
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


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", os.getenv("DASHSCOPE_API_KEY", "test-key"))
    for proxy_key in [
        "ALL_PROXY",
        "all_proxy",
        "HTTP_PROXY",
        "http_proxy",
        "HTTPS_PROXY",
        "https_proxy",
    ]:
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


def create_user(username: str, role_name: str = UserRole.USER) -> User:
    role = UserRole.query.filter_by(name=role_name).first()
    if role is None:
        role = UserRole(name=role_name, description=f"{role_name} role")
        db.session.add(role)
        db.session.flush()

    user = User(
        username=username,
        email=f"{username}@connect.hkust-gz.edu.cn",
        role_id=role.id,
        email_verified=True,
    )
    user.password_hash = "test-password-hash"
    db.session.add(user)
    db.session.flush()
    return user


def create_feedback(
    author_id: int,
    title: str,
    status: str,
    markdown_content: str,
) -> Feedback:
    feedback = Feedback(author_id=author_id, title=title, status=status)
    db.session.add(feedback)
    db.session.flush()

    version = FeedbackVersion(
        feedback_id=feedback.id,
        version_number=1,
        markdown_content=markdown_content,
        created_by_user_id=author_id,
    )
    db.session.add(version)
    db.session.flush()

    feedback.current_version_id = version.id
    db.session.commit()
    return feedback


def auth_headers(user_id: int) -> dict[str, str]:
    token = create_access_token(identity=str(user_id))
    return {"Authorization": f"Bearer {token}"}


def test_create_feedback_requires_auth(app, client):
    with app.app_context():
        response = client.post(
            "/feedbacks",
            json={"title": "Need more outlets", "markdown_content": "Body"},
        )

    assert response.status_code == 401


def test_create_feedback_creates_pending_submission_with_initial_version(app, client):
    with app.app_context():
        author = create_user("feedback_author")
        headers = auth_headers(author.id)

        response = client.post(
            "/feedbacks",
            json={
                "title": "Need more outlets",
                "markdown_content": "Please add more sockets in the library.",
            },
            headers=headers,
        )

        payload = response.get_json()
        stored_feedback = Feedback.query.get(payload["id"])
        versions = stored_feedback.versions.order_by(FeedbackVersion.version_number.asc()).all()

    assert response.status_code == 201
    assert payload["status"] == Feedback.STATUS_PENDING_REVIEW
    assert payload["current_version"]["version_number"] == 1
    assert payload["current_version"]["markdown_content"] == "Please add more sockets in the library."
    assert stored_feedback.current_version_id == versions[0].id
    assert len(versions) == 1


def test_public_list_only_returns_published_feedback(app, client):
    with app.app_context():
        author = create_user("public_list_author")
        published = create_feedback(
            author_id=author.id,
            title="Add more shuttle maps",
            status=Feedback.STATUS_PUBLISHED,
            markdown_content="Published body",
        )
        create_feedback(
            author_id=author.id,
            title="Dorm laundry tracker",
            status=Feedback.STATUS_REJECTED,
            markdown_content="Rejected body",
        )
        create_feedback(
            author_id=author.id,
            title="Quiet room sensors",
            status=Feedback.STATUS_PENDING_REVIEW,
            markdown_content="Pending body",
        )

        response = client.get("/feedbacks")
        payload = response.get_json()

    assert response.status_code == 200
    assert [item["id"] for item in payload["feedbacks"]] == [published.id]


def test_feedback_detail_exposes_published_feedback(app, client):
    with app.app_context():
        author = create_user("published_detail_author")
        feedback = create_feedback(
            author_id=author.id,
            title="Improve night bus signage",
            status=Feedback.STATUS_PUBLISHED,
            markdown_content="## Current text",
        )

        response = client.get(f"/feedbacks/{feedback.id}")
        payload = response.get_json()

    assert response.status_code == 200
    assert payload["id"] == feedback.id
    assert payload["current_version"]["markdown_content"] == "## Current text"


def test_feedback_detail_hides_unpublished_feedback_from_other_users(app, client):
    with app.app_context():
        author = create_user("feedback_owner_private")
        viewer = create_user("feedback_viewer")
        feedback = create_feedback(
            author_id=author.id,
            title="Private draft",
            status=Feedback.STATUS_PENDING_REVIEW,
            markdown_content="Not public yet",
        )

        response = client.get(
            f"/feedbacks/{feedback.id}",
            headers=auth_headers(viewer.id),
        )

    assert response.status_code == 404


def test_feedback_version_history_is_public_for_published_feedback(app, client):
    with app.app_context():
        author = create_user("version_owner")
        feedback = create_feedback(
            author_id=author.id,
            title="Version history request",
            status=Feedback.STATUS_PUBLISHED,
            markdown_content="Version one",
        )
        second_version = FeedbackVersion(
            feedback_id=feedback.id,
            version_number=2,
            markdown_content="Version two",
            created_by_user_id=author.id,
        )
        db.session.add(second_version)
        db.session.flush()
        feedback.current_version_id = second_version.id
        db.session.commit()

        response = client.get(f"/feedbacks/{feedback.id}/versions")
        payload = response.get_json()

    assert response.status_code == 200
    assert [item["version_number"] for item in payload["versions"]] == [1, 2]


def test_my_feedbacks_returns_private_feedback_for_owner(app, client):
    with app.app_context():
        owner = create_user("my_feedback_owner")
        other_user = create_user("another_feedback_owner")
        own_pending = create_feedback(
            author_id=owner.id,
            title="My pending feedback",
            status=Feedback.STATUS_PENDING_REVIEW,
            markdown_content="Pending content",
        )
        own_rejected = create_feedback(
            author_id=owner.id,
            title="My rejected feedback",
            status=Feedback.STATUS_REJECTED,
            markdown_content="Rejected content",
        )
        create_feedback(
            author_id=other_user.id,
            title="Someone else's feedback",
            status=Feedback.STATUS_PENDING_REVIEW,
            markdown_content="Other content",
        )

        response = client.get("/feedbacks/mine", headers=auth_headers(owner.id))
        payload = response.get_json()

    assert response.status_code == 200
    assert [item["id"] for item in payload["feedbacks"]] == [own_rejected.id, own_pending.id]
