import os
from typing import Tuple

import pytest
from flask_jwt_extended import create_access_token
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles

from app import create_app
from app.config import Config
from app.extensions import db
from app.models.feedback import Feedback
from app.models.feedback_merge_comment import FeedbackMergeComment
from app.models.feedback_merge_request import FeedbackMergeRequest
from app.models.feedback_version import FeedbackVersion
from app.models.user import User
from app.models.user_role import UserRole
from app.services.feedback_service import FeedbackService


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


def create_feedback(author_id: int, title: str, markdown_content: str) -> Tuple[Feedback, FeedbackVersion]:
    feedback = Feedback(
        author_id=author_id,
        title=title,
        status=Feedback.STATUS_PUBLISHED,
    )
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
    return feedback, version


def auth_headers(user_id: int) -> dict[str, str]:
    token = create_access_token(identity=str(user_id))
    return {"Authorization": f"Bearer {token}"}


def create_merge_request(
    feedback_id: int,
    proposer_id: int,
    base_version_id: int,
    proposed_markdown_content: str,
    change_summary: str = "clarify title",
    status: str = FeedbackMergeRequest.STATUS_OPEN,
) -> FeedbackMergeRequest:
    merge_request = FeedbackMergeRequest(
        feedback_id=feedback_id,
        author_id=proposer_id,
        base_version_id=base_version_id,
        title="Improve wording",
        change_summary=change_summary,
        proposed_markdown_content=proposed_markdown_content,
        status=status,
    )
    db.session.add(merge_request)
    db.session.commit()
    return merge_request


def test_merge_request_starts_with_current_markdown(app, client):
    with app.app_context():
        owner = create_user("feedback_merge_owner")
        proposer = create_user("feedback_merge_proposer")
        feedback, current_version = create_feedback(owner.id, "Bus stop issue", "old body")
        current_version_id = current_version.id

        response = client.post(
            f"/feedbacks/{feedback.id}/merge-requests",
            json={
                "change_summary": "clarify title",
                "proposed_markdown_content": current_version.markdown_content.replace("old", "new"),
            },
            headers=auth_headers(proposer.id),
        )
        payload = response.get_json()

    assert response.status_code == 201
    assert payload["base_version_id"] == current_version_id
    assert payload["status"] == FeedbackMergeRequest.STATUS_OPEN


def test_proposer_can_withdraw_open_merge_request(app, client):
    with app.app_context():
        owner = create_user("withdraw_feedback_owner")
        proposer = create_user("withdraw_feedback_proposer")
        feedback, current_version = create_feedback(owner.id, "Lamp feedback", "base text")
        merge_request = create_merge_request(
            feedback_id=feedback.id,
            proposer_id=proposer.id,
            base_version_id=current_version.id,
            proposed_markdown_content="updated text",
        )

        response = client.post(
            f"/merge-requests/{merge_request.id}/withdraw",
            headers=auth_headers(proposer.id),
        )
        payload = response.get_json()

    assert response.status_code == 200
    assert payload["status"] == FeedbackMergeRequest.STATUS_WITHDRAWN


def test_author_request_changes_updates_state(app, client):
    with app.app_context():
        owner = create_user("author_request_changes_owner")
        proposer = create_user("author_request_changes_proposer")
        feedback, current_version = create_feedback(owner.id, "Path lighting", "base text")
        merge_request = create_merge_request(
            feedback_id=feedback.id,
            proposer_id=proposer.id,
            base_version_id=current_version.id,
            proposed_markdown_content="updated text",
        )

        response = client.post(
            f"/merge-requests/{merge_request.id}/request-changes",
            json={"note": "Please keep the tone more neutral."},
            headers=auth_headers(owner.id),
        )
        payload = response.get_json()

    assert response.status_code == 200
    assert payload["status"] == FeedbackMergeRequest.STATUS_AUTHOR_CHANGES_REQUESTED
    assert payload["author_review_note"] == "Please keep the tone more neutral."


def test_author_reject_updates_state(app, client):
    with app.app_context():
        owner = create_user("author_reject_owner")
        proposer = create_user("author_reject_proposer")
        feedback, current_version = create_feedback(owner.id, "Classroom signs", "base text")
        merge_request = create_merge_request(
            feedback_id=feedback.id,
            proposer_id=proposer.id,
            base_version_id=current_version.id,
            proposed_markdown_content="updated text",
        )

        response = client.post(
            f"/merge-requests/{merge_request.id}/reject",
            json={"note": "This changes the original intent too much."},
            headers=auth_headers(owner.id),
        )
        payload = response.get_json()

    assert response.status_code == 200
    assert payload["status"] == FeedbackMergeRequest.STATUS_AUTHOR_REJECTED


def test_author_accept_moves_request_to_admin_queue(app, client):
    with app.app_context():
        owner = create_user("author_accept_owner")
        proposer = create_user("author_accept_proposer")
        feedback, current_version = create_feedback(owner.id, "Gym equipment", "old body")
        merge_request = create_merge_request(
            feedback_id=feedback.id,
            proposer_id=proposer.id,
            base_version_id=current_version.id,
            proposed_markdown_content="new body",
        )

        response = client.post(
            f"/merge-requests/{merge_request.id}/accept",
            json={"note": "looks good"},
            headers=auth_headers(owner.id),
        )
        payload = response.get_json()

    assert response.status_code == 200
    assert payload["status"] == FeedbackMergeRequest.STATUS_AUTHOR_ACCEPTED_PENDING_ADMIN
    assert payload["author_review_note"] == "looks good"


def test_feedback_author_can_update_merge_request_content_before_accept(app, client):
    with app.app_context():
        owner = create_user("author_updates_merge_owner")
        proposer = create_user("author_updates_merge_proposer")
        feedback, current_version = create_feedback(owner.id, "Dorm laundry", "old body")
        merge_request = create_merge_request(
            feedback_id=feedback.id,
            proposer_id=proposer.id,
            base_version_id=current_version.id,
            proposed_markdown_content="draft body",
        )

        response = client.put(
            f"/merge-requests/{merge_request.id}/proposed-content",
            json={"proposed_markdown_content": "author edited body"},
            headers=auth_headers(owner.id),
        )
        payload = response.get_json()

    assert response.status_code == 200
    assert payload["proposed_markdown_content"] == "author edited body"
    assert payload["status"] == FeedbackMergeRequest.STATUS_OPEN


def test_public_discussion_can_comment_on_merge_request_detail(app, client):
    with app.app_context():
        owner = create_user("merge_comment_owner")
        proposer = create_user("merge_comment_proposer")
        commenter = create_user("merge_comment_participant")
        commenter_id = commenter.id
        feedback, current_version = create_feedback(owner.id, "Campus shuttle", "base text")
        merge_request = create_merge_request(
            feedback_id=feedback.id,
            proposer_id=proposer.id,
            base_version_id=current_version.id,
            proposed_markdown_content="updated text",
        )

        response = client.post(
            f"/merge-requests/{merge_request.id}/comments",
            json={"content": "I support this wording."},
            headers=auth_headers(commenter.id),
        )
        payload = response.get_json()

        stored_comment = FeedbackMergeComment.query.get(payload["id"])

    assert response.status_code == 201
    assert payload["merge_request_id"] == merge_request.id
    assert payload["content"] == "I support this wording."
    assert stored_comment is not None
    assert stored_comment.user_id == commenter_id


def test_admin_merge_request_creates_new_feedback_version(app):
    with app.app_context():
        owner = create_user("admin_merge_owner")
        proposer = create_user("admin_merge_proposer")
        admin = create_user("admin_merge_admin", role_name=UserRole.ADMIN)
        feedback, current_version = create_feedback(owner.id, "Printer queue", "old body")
        merge_request = create_merge_request(
            feedback_id=feedback.id,
            proposer_id=proposer.id,
            base_version_id=current_version.id,
            proposed_markdown_content="new body",
            status=FeedbackMergeRequest.STATUS_AUTHOR_ACCEPTED_PENDING_ADMIN,
        )

        merged_version = FeedbackService.admin_merge_request(
            merge_request_id=merge_request.id,
            admin_user_id=admin.id,
            note="Approved for publication",
        )
        db.session.refresh(merge_request)
        db.session.refresh(feedback)
        merged_version_number = merged_version.version_number
        merged_version_id = merged_version.id
        merge_request_status = merge_request.status
        feedback_current_version_id = feedback.current_version_id
        merge_request_merged_version_id = merge_request.merged_version_id

    assert merged_version_number == 2
    assert merge_request_status == FeedbackMergeRequest.STATUS_MERGED
    assert merge_request_merged_version_id == merged_version_id
    assert feedback_current_version_id == merged_version_id
