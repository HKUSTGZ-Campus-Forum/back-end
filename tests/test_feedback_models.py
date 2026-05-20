import os

import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles

from app import create_app, _ensure_mount_admin_role, _seed_dev_feedback
from app.config import Config
from app.extensions import db
from app.models.user import User
from app.models.user_role import UserRole
from app.models.feedback import Feedback
from app.models.feedback_version import FeedbackVersion
from app.models.feedback_merge_request import FeedbackMergeRequest


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


def test_feedback_version_numbers_increment(app):
    owner = create_user("feedback_owner")
    feedback = Feedback(
        author_id=owner.id,
        title="Dorm printer issue",
        status=Feedback.STATUS_PUBLISHED,
    )
    db.session.add(feedback)
    db.session.flush()

    version_one = FeedbackVersion(
        feedback_id=feedback.id,
        version_number=1,
        markdown_content="Initial body",
        created_by_user_id=owner.id,
    )
    version_two = FeedbackVersion(
        feedback_id=feedback.id,
        version_number=2,
        markdown_content="Updated body",
        created_by_user_id=owner.id,
    )
    db.session.add_all([version_one, version_two])
    db.session.commit()

    ordered_versions = feedback.versions.order_by(FeedbackVersion.version_number.asc()).all()

    assert [version.version_number for version in ordered_versions] == [1, 2]
    assert ordered_versions[-1].markdown_content == "Updated body"


def test_merge_request_tracks_base_version_and_final_version(app):
    owner = create_user("feedback_owner_two")
    proposer = create_user("feedback_editor")

    feedback = Feedback(
        author_id=owner.id,
        title="Better shuttle stop signs",
        status=Feedback.STATUS_PUBLISHED,
    )
    db.session.add(feedback)
    db.session.flush()

    base_version = FeedbackVersion(
        feedback_id=feedback.id,
        version_number=1,
        markdown_content="Current published body",
        created_by_user_id=owner.id,
    )
    db.session.add(base_version)
    db.session.flush()

    merge_request = FeedbackMergeRequest(
        feedback_id=feedback.id,
        author_id=proposer.id,
        base_version_id=base_version.id,
        title="Clarify scope",
        change_summary="Make the ask more concrete",
        proposed_markdown_content="## Revised body",
        status=FeedbackMergeRequest.STATUS_OPEN,
    )
    db.session.add(merge_request)
    db.session.commit()

    assert merge_request.base_version_id == base_version.id
    assert merge_request.merged_version_id is None
    assert merge_request.status == FeedbackMergeRequest.STATUS_OPEN


def test_mount_uid_is_promoted_to_admin_when_helper_runs(app):
    user_role = UserRole.query.filter_by(name=UserRole.USER).first()
    if user_role is None:
        user_role = UserRole(name=UserRole.USER, description="user role")
        db.session.add(user_role)
        db.session.flush()

    admin_role = UserRole.query.filter_by(name=UserRole.ADMIN).first()
    if admin_role is None:
        admin_role = UserRole(name=UserRole.ADMIN, description="admin role")
        db.session.add(admin_role)
        db.session.flush()

    mount_user = User(
        id=6,
        username="Mount",
        email="mount@example.com",
        role_id=user_role.id,
        email_verified=True,
    )
    mount_user.password_hash = "test-password-hash"
    db.session.add(mount_user)
    db.session.commit()

    _ensure_mount_admin_role()

    promoted_user = db.session.get(User, 6)
    assert promoted_user.role_id == admin_role.id
    assert promoted_user.is_admin() is True


def test_dev_seed_feedback_is_idempotent(app):
    user_role = UserRole.query.filter_by(name=UserRole.USER).first()
    if user_role is None:
        user_role = UserRole(name=UserRole.USER, description="user role")
        db.session.add(user_role)
        db.session.flush()

    mount_user = User(
        id=6,
        username="Mount",
        email="mount-dev@example.com",
        role_id=user_role.id,
        email_verified=True,
    )
    mount_user.password_hash = "test-password-hash"
    db.session.add(mount_user)
    db.session.commit()

    app.config["FRONTEND_BASE_URL"] = "https://dev.unikorn.axfff.com"

    _seed_dev_feedback()
    _seed_dev_feedback()

    seeded_feedbacks = Feedback.query.filter_by(title="[DEV] Feedback flow smoke test").all()
    assert len(seeded_feedbacks) == 1
    assert seeded_feedbacks[0].status == Feedback.STATUS_PUBLISHED
