import os

import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles

from app import create_app
from app.config import Config
from app.extensions import db
from app.models.feedback import Feedback
from app.models.feedback_version import FeedbackVersion
from app.models.notification import Notification
from app.models.user import User
from app.models.user_role import UserRole
from app.services.feedback_service import FeedbackService
from app.services.notification_service import NotificationService


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
        monkeypatch.setattr(NotificationService, "_send_push_notification", staticmethod(lambda notification: None))
        yield app
        db.session.remove()
        db.drop_all()


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


def create_feedback(author_id: int, title: str, markdown_content: str, status: str = Feedback.STATUS_PUBLISHED):
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


def test_merge_request_creation_notifies_feedback_author(app):
    owner = create_user("feedback_notify_owner")
    proposer = create_user("feedback_notify_proposer")
    feedback = create_feedback(owner.id, "Need more outlets", "body")

    merge_request = FeedbackService.create_merge_request(
        feedback_id=feedback.id,
        proposer_id=proposer.id,
        change_summary="clarify request",
        proposed_markdown_content="updated body",
    )

    notification = Notification.query.filter_by(
        recipient_id=owner.id,
        type="feedback_merge_request_created",
    ).first()

    assert notification is not None
    assert notification.link_url == f"/feedback/merge-requests/{merge_request.id}"


def test_author_accepting_merge_request_notifies_admin_queue(app):
    owner = create_user("feedback_notify_owner_two")
    proposer = create_user("feedback_notify_proposer_two")
    admin = create_user("feedback_notify_admin", role_name=UserRole.ADMIN)
    feedback = create_feedback(owner.id, "Need more bike racks", "body")
    merge_request = FeedbackService.create_merge_request(
        feedback_id=feedback.id,
        proposer_id=proposer.id,
        change_summary="clarify request",
        proposed_markdown_content="updated body",
    )

    FeedbackService.author_accept_merge_request(
        merge_request_id=merge_request.id,
        author_user_id=owner.id,
        note="looks good",
    )

    notification = Notification.query.filter_by(
        recipient_id=admin.id,
        type="feedback_merge_request_ready_for_admin",
    ).first()

    assert notification is not None
    assert notification.link_url == "/admin/feedback"


def test_feedback_publication_notifies_feedback_author(app):
    owner = create_user("feedback_publish_owner")
    admin = create_user("feedback_publish_admin", role_name=UserRole.ADMIN)
    feedback = create_feedback(
        owner.id,
        "Need more quiet rooms",
        "body",
        status=Feedback.STATUS_PENDING_REVIEW,
    )

    FeedbackService.publish_feedback(feedback_id=feedback.id, admin_user_id=admin.id)

    notification = Notification.query.filter_by(
        recipient_id=owner.id,
        type="feedback_published",
    ).first()

    assert notification is not None
    assert notification.link_url == f"/feedback/{feedback.id}"
