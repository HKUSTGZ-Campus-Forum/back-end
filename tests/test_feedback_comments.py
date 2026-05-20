import os

import pytest
from flask_jwt_extended import create_access_token
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles

from app import create_app
from app.config import Config
from app.extensions import db
from app.models.feedback import Feedback
from app.models.feedback_comment import FeedbackComment
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


def create_feedback(author_id: int, title: str, status: str, markdown_content: str) -> Feedback:
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


def test_feedback_comment_creation_blocks_when_comments_ended(app, client):
    with app.app_context():
        author = create_user("feedback_owner")
        commenter = create_user("feedback_commenter")
        feedback = create_feedback(
            author_id=author.id,
            title="Published feedback",
            status=Feedback.STATUS_PUBLISHED,
            markdown_content="Body",
        )
        feedback.comments_ended = True
        db.session.commit()

        response = client.post(
            f"/feedbacks/{feedback.id}/comments",
            json={"content": "still trying"},
            headers=auth_headers(commenter.id),
        )

    assert response.status_code == 400


def test_feedback_comment_creation_supports_replies(app, client):
    with app.app_context():
        author = create_user("reply_feedback_owner")
        commenter = create_user("reply_feedback_commenter")
        feedback = create_feedback(
            author_id=author.id,
            title="Reply feedback",
            status=Feedback.STATUS_PUBLISHED,
            markdown_content="Body",
        )

        parent_response = client.post(
            f"/feedbacks/{feedback.id}/comments",
            json={"content": "Parent comment"},
            headers=auth_headers(commenter.id),
        )
        parent_payload = parent_response.get_json()

        reply_response = client.post(
            f"/feedbacks/{feedback.id}/comments",
            json={
                "content": "Reply comment",
                "parent_comment_id": parent_payload["id"],
            },
            headers=auth_headers(commenter.id),
        )
        reply_payload = reply_response.get_json()

    assert parent_response.status_code == 201
    assert reply_response.status_code == 201
    assert reply_payload["parent_comment_id"] == parent_payload["id"]


def test_hidden_comment_masks_content_for_regular_users(app, client):
    with app.app_context():
        author = create_user("hidden_feedback_owner")
        commenter = create_user("hidden_comment_author")
        viewer = create_user("hidden_comment_viewer")
        feedback = create_feedback(
            author_id=author.id,
            title="Hidden comment feedback",
            status=Feedback.STATUS_PUBLISHED,
            markdown_content="Body",
        )
        comment = FeedbackComment(
            feedback_id=feedback.id,
            user_id=commenter.id,
            content="Original hidden comment",
            visibility=FeedbackComment.VISIBILITY_HIDDEN,
            hidden_reason="abusive",
        )
        db.session.add(comment)
        db.session.commit()

        response = client.get(
            f"/feedbacks/{feedback.id}",
            headers=auth_headers(viewer.id),
        )
        payload = response.get_json()

    assert response.status_code == 200
    assert payload["comments"][0]["content"] == "该评论因管理原因被隐藏"
    assert payload["comments"][0]["hidden_reason"] is None


def test_hidden_comment_keeps_reason_for_comment_owner(app, client):
    with app.app_context():
        author = create_user("owner_feedback_author")
        commenter = create_user("owner_comment_author")
        feedback = create_feedback(
            author_id=author.id,
            title="Owner comment feedback",
            status=Feedback.STATUS_PUBLISHED,
            markdown_content="Body",
        )
        comment = FeedbackComment(
            feedback_id=feedback.id,
            user_id=commenter.id,
            content="Owner can still read this",
            visibility=FeedbackComment.VISIBILITY_HIDDEN,
            hidden_reason="abusive",
        )
        db.session.add(comment)
        db.session.commit()

        response = client.get(
            f"/feedbacks/{feedback.id}",
            headers=auth_headers(commenter.id),
        )
        payload = response.get_json()

    assert response.status_code == 200
    assert payload["comments"][0]["content"] == "Owner can still read this"
    assert payload["comments"][0]["hidden_reason"] == "abusive"
