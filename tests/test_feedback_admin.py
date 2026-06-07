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
from app.models.feedback_comment import FeedbackComment
from app.models.feedback_merge_request import FeedbackMergeRequest
from app.models.feedback_version import FeedbackVersion
from app.models.user import User
from app.models.user_role import UserRole


@compiles(JSONB, "sqlite")
def compile_jsonb_sqlite(_type, _compiler, **_kw):
    return "JSON"


class TestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_ENGINE_OPTIONS = {}
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
    markdown_content: str,
    status: str = Feedback.STATUS_PENDING_REVIEW,
) -> Tuple[Feedback, FeedbackVersion]:
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
    return feedback, version


def create_merge_request(
    feedback_id: int,
    proposer_id: int,
    base_version_id: int,
    proposed_markdown_content: str,
    status: str = FeedbackMergeRequest.STATUS_AUTHOR_ACCEPTED_PENDING_ADMIN,
) -> FeedbackMergeRequest:
    merge_request = FeedbackMergeRequest(
        feedback_id=feedback_id,
        author_id=proposer_id,
        base_version_id=base_version_id,
        title="Improve wording",
        change_summary="clarify title",
        proposed_markdown_content=proposed_markdown_content,
        status=status,
    )
    db.session.add(merge_request)
    db.session.commit()
    return merge_request


def auth_headers(user_id: int) -> dict[str, str]:
    token = create_access_token(identity=str(user_id))
    return {"Authorization": f"Bearer {token}"}


def test_admin_can_publish_pending_feedback(app, client):
    with app.app_context():
        admin = create_user("feedback_admin", role_name=UserRole.ADMIN)
        owner, _ = create_feedback(
            author_id=create_user("pending_feedback_owner").id,
            title="Need more outlets",
            markdown_content="Body",
            status=Feedback.STATUS_PENDING_REVIEW,
        )

        response = client.post(
            f"/admin/feedbacks/{owner.id}/approve",
            headers=auth_headers(admin.id),
        )
        payload = response.get_json()

    assert response.status_code == 200
    assert payload["status"] == Feedback.STATUS_PUBLISHED


def test_admin_can_approve_author_accepted_merge_request(app, client):
    with app.app_context():
        admin = create_user("merge_admin", role_name=UserRole.ADMIN)
        feedback_owner = create_user("merge_feedback_owner")
        proposer = create_user("merge_feedback_proposer")
        feedback, version = create_feedback(
            author_id=feedback_owner.id,
            title="Need more shuttle signs",
            markdown_content="old body",
            status=Feedback.STATUS_PUBLISHED,
        )
        merge_request = create_merge_request(
            feedback_id=feedback.id,
            proposer_id=proposer.id,
            base_version_id=version.id,
            proposed_markdown_content="new body",
        )

        response = client.post(
            f"/admin/merge-requests/{merge_request.id}/approve",
            json={"note": "Approved"},
            headers=auth_headers(admin.id),
        )
        payload = response.get_json()

    assert response.status_code == 200
    assert payload["status"] == FeedbackMergeRequest.STATUS_MERGED
    assert payload["merged_version_id"] is not None


def test_admin_can_close_feedback(app, client):
    with app.app_context():
        admin = create_user("close_feedback_admin", role_name=UserRole.ADMIN)
        owner = create_user("close_feedback_owner")
        feedback, _ = create_feedback(
            author_id=owner.id,
            title="Need more bike racks",
            markdown_content="Body",
            status=Feedback.STATUS_PUBLISHED,
        )

        response = client.post(
            f"/admin/feedbacks/{feedback.id}/close",
            headers=auth_headers(admin.id),
        )
        payload = response.get_json()

    assert response.status_code == 200
    assert payload["status"] == Feedback.STATUS_CLOSED


def test_admin_can_end_feedback_comments(app, client):
    with app.app_context():
        admin = create_user("end_comments_admin", role_name=UserRole.ADMIN)
        owner = create_user("end_comments_owner")
        feedback, _ = create_feedback(
            author_id=owner.id,
            title="Need more study rooms",
            markdown_content="Body",
            status=Feedback.STATUS_PUBLISHED,
        )

        response = client.post(
            f"/admin/feedbacks/{feedback.id}/end-comments",
            headers=auth_headers(admin.id),
        )
        payload = response.get_json()

    assert response.status_code == 200
    assert payload["comments_ended"] is True


def test_admin_can_hide_feedback_comment(app, client):
    with app.app_context():
        admin = create_user("hide_comment_admin", role_name=UserRole.ADMIN)
        owner = create_user("hide_comment_owner")
        commenter = create_user("hide_comment_author")
        feedback, _ = create_feedback(
            author_id=owner.id,
            title="Need more seats",
            markdown_content="Body",
            status=Feedback.STATUS_PUBLISHED,
        )
        comment = FeedbackComment(
            feedback_id=feedback.id,
            user_id=commenter.id,
            content="very rude comment",
        )
        db.session.add(comment)
        db.session.commit()

        response = client.post(
            f"/admin/feedback-comments/{comment.id}/hide",
            json={"reason": "personal attack"},
            headers=auth_headers(admin.id),
        )
        payload = response.get_json()

    assert response.status_code == 200
    assert payload["visibility"] == FeedbackComment.VISIBILITY_HIDDEN
    assert payload["hidden_reason"] == "personal attack"


def test_admin_can_list_feedbacks_by_status(app, client):
    with app.app_context():
        admin = create_user("feedback_list_admin", role_name=UserRole.ADMIN)
        pending, _ = create_feedback(
            author_id=create_user("feedback_list_pending_owner").id,
            title="Pending feedback",
            markdown_content="Pending body",
            status=Feedback.STATUS_PENDING_REVIEW,
        )
        published, _ = create_feedback(
            author_id=create_user("feedback_list_published_owner").id,
            title="Published feedback",
            markdown_content="Published body",
            status=Feedback.STATUS_PUBLISHED,
        )
        create_feedback(
            author_id=create_user("feedback_list_closed_owner").id,
            title="Closed feedback",
            markdown_content="Closed body",
            status=Feedback.STATUS_CLOSED,
        )

        response = client.get(
            "/admin/feedbacks?status=published",
            headers=auth_headers(admin.id),
        )
        payload = response.get_json()

    assert response.status_code == 200
    assert [item["id"] for item in payload["feedbacks"]] == [published.id]
    assert payload["total"] == 1
    assert payload["counts"]["pending_review"] == 1
    assert payload["counts"]["published"] == 1
    assert payload["counts"]["closed"] == 1
    assert payload["counts"]["total"] == 3


def test_admin_can_list_merge_requests_by_status(app, client):
    with app.app_context():
        admin = create_user("merge_list_admin", role_name=UserRole.ADMIN)
        feedback_owner = create_user("merge_list_feedback_owner")
        proposer = create_user("merge_list_proposer")
        feedback, version = create_feedback(
            author_id=feedback_owner.id,
            title="Feedback with merge requests",
            markdown_content="base",
            status=Feedback.STATUS_PUBLISHED,
        )
        pending = create_merge_request(
            feedback_id=feedback.id,
            proposer_id=proposer.id,
            base_version_id=version.id,
            proposed_markdown_content="pending body",
            status=FeedbackMergeRequest.STATUS_AUTHOR_ACCEPTED_PENDING_ADMIN,
        )
        create_merge_request(
            feedback_id=feedback.id,
            proposer_id=proposer.id,
            base_version_id=version.id,
            proposed_markdown_content="merged body",
            status=FeedbackMergeRequest.STATUS_MERGED,
        )

        response = client.get(
            "/admin/merge-requests?status=author_accepted_pending_admin",
            headers=auth_headers(admin.id),
        )
        payload = response.get_json()

    assert response.status_code == 200
    assert [item["id"] for item in payload["merge_requests"]] == [pending.id]
    assert payload["total"] == 1
    assert payload["counts"]["author_accepted_pending_admin"] == 1
    assert payload["counts"]["merged"] == 1
    assert payload["counts"]["total"] == 2


def test_non_admin_cannot_list_feedback_admin_history(app, client):
    with app.app_context():
        user = create_user("feedback_list_regular_user")

        feedback_response = client.get("/admin/feedbacks", headers=auth_headers(user.id))
        merge_response = client.get("/admin/merge-requests", headers=auth_headers(user.id))

    assert feedback_response.status_code == 403
    assert merge_response.status_code == 403
