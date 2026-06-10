import os

import pytest
from flask_jwt_extended import create_access_token
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles

from app import create_app
from app.config import Config
from app.extensions import db
from app.models.comment import Comment
from app.models.admin_audit_log import AdminAuditLog
from app.models.feedback import Feedback
from app.models.feedback_merge_request import FeedbackMergeRequest
from app.models.feedback_version import FeedbackVersion
from app.models.file import File
from app.models.gugu_message import GuguMessage
from app.models.identity_type import IdentityType
from app.models.post import Post
from app.models.user import User
from app.models.user_identity import UserIdentity
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


def auth_headers(user_id: int) -> dict[str, str]:
    token = create_access_token(identity=str(user_id))
    return {"Authorization": f"Bearer {token}"}


SUMMARY_ENDPOINTS = [
    (
        "/admin/courses/summary",
        "courses",
        {"courses", "active_courses", "offerings", "sections", "meetings"},
    ),
    (
        "/admin/matching/summary",
        "matching",
        {"projects", "active_projects", "profiles", "active_profiles"},
    ),
    (
        "/admin/contest/summary",
        "contest",
        {"contests", "active_contests", "organizers", "submissions"},
    ),
    (
        "/admin/operations/summary",
        "operations",
        {
            "files",
            "sts_tokens",
            "valid_sts_tokens",
            "oauth_clients",
            "oauth_tokens",
            "notifications",
            "unread_notifications",
            "push_subscriptions",
        },
    ),
]


def create_feedback(author: User, status: str = Feedback.STATUS_PENDING_REVIEW) -> Feedback:
    feedback = Feedback(author_id=author.id, title="Campus feedback", status=status)
    db.session.add(feedback)
    db.session.flush()
    version = FeedbackVersion(
        feedback_id=feedback.id,
        version_number=1,
        markdown_content="Please improve this.",
        created_by_user_id=author.id,
    )
    db.session.add(version)
    db.session.flush()
    feedback.current_version_id = version.id
    return feedback


def seed_admin_console_data():
    admin = create_user("admin_console_admin", UserRole.ADMIN)
    user = create_user("admin_console_user")
    if UserRole.query.filter_by(name=UserRole.MODERATOR).first() is None:
        db.session.add(UserRole(name=UserRole.MODERATOR, description="moderator role"))
        db.session.flush()
    post = Post(user_id=user.id, title="A post to moderate", content="content")
    db.session.add(post)
    db.session.flush()
    comment = Comment(post_id=post.id, user_id=user.id, content="comment body")
    db.session.add(comment)
    db.session.add(GuguMessage(author_id=user.id, content="gugu body"))
    db.session.add(File(
        user_id=user.id,
        object_name="admin-console/file.txt",
        original_filename="file.txt",
        file_size=10,
        mime_type="text/plain",
        status="uploaded",
    ))
    feedback = create_feedback(user)
    merge_request = FeedbackMergeRequest(
        feedback_id=feedback.id,
        author_id=user.id,
        base_version_id=feedback.current_version_id,
        title="Improve feedback",
        change_summary="clearer",
        proposed_markdown_content="Updated",
        status=FeedbackMergeRequest.STATUS_AUTHOR_ACCEPTED_PENDING_ADMIN,
    )
    identity_type = IdentityType(
        name="student",
        display_name="Student",
        color="#2563eb",
        icon_name="user",
        description="Student",
    )
    db.session.add(identity_type)
    db.session.flush()
    db.session.add(UserIdentity(
        user_id=user.id,
        identity_type_id=identity_type.id,
        status=UserIdentity.PENDING,
    ))
    db.session.add(merge_request)
    db.session.commit()
    return admin, user, post, comment


def test_admin_overview_returns_full_site_metrics(app, client):
    with app.app_context():
        admin, _user, _post, _comment = seed_admin_console_data()
        response = client.get("/admin/overview", headers=auth_headers(admin.id))
        payload = response.get_json()

    assert response.status_code == 200
    assert payload["metrics"]["users"]["total"] == 2
    assert payload["metrics"]["content"]["posts"]["total"] == 1
    assert payload["metrics"]["content"]["comments"]["total"] == 1
    assert payload["pending"]["feedbacks"] == 1
    assert payload["pending"]["merge_requests"] == 1
    assert payload["pending"]["identity_requests"] == 1
    assert "courses" in payload["metrics"]
    assert "operations" in payload["metrics"]


def test_admin_overview_trends_returns_daily_campus_metrics(app, client):
    with app.app_context():
        admin, _user, _post, _comment = seed_admin_console_data()
        response = client.get("/admin/overview/trends?days=7", headers=auth_headers(admin.id))
        payload = response.get_json()

    assert response.status_code == 200
    assert payload["days"] == 7
    assert len(payload["items"]) == 7
    assert {
        "date",
        "users",
        "posts",
        "comments",
        "feedbacks",
        "identity_requests",
        "course_records",
        "projects",
        "files",
    }.issubset(payload["items"][0].keys())


def test_admin_overview_trends_rejects_unsupported_days(app, client):
    with app.app_context():
        admin = create_user("trend_admin", UserRole.ADMIN)
        response = client.get("/admin/overview/trends?days=14", headers=auth_headers(admin.id))

    assert response.status_code == 400


def test_non_admin_cannot_access_admin_overview(app, client):
    with app.app_context():
        user = create_user("regular_console_user")
        response = client.get("/admin/overview", headers=auth_headers(user.id))

    assert response.status_code == 403


@pytest.mark.parametrize("endpoint, _metric_key, _expected_keys", SUMMARY_ENDPOINTS)
def test_non_admin_cannot_access_admin_summary_endpoints(app, client, endpoint, _metric_key, _expected_keys):
    with app.app_context():
        user = create_user("regular_summary_user")
        response = client.get(endpoint, headers=auth_headers(user.id))

    assert response.status_code == 403


@pytest.mark.parametrize("endpoint, metric_key, expected_keys", SUMMARY_ENDPOINTS)
def test_admin_summary_endpoints_match_overview_metric_shape(app, client, endpoint, metric_key, expected_keys):
    with app.app_context():
        admin, _user, _post, _comment = seed_admin_console_data()
        summary_response = client.get(endpoint, headers=auth_headers(admin.id))
        overview_response = client.get("/admin/overview", headers=auth_headers(admin.id))
        summary_payload = summary_response.get_json()
        overview_payload = overview_response.get_json()

    assert summary_response.status_code == 200
    assert overview_response.status_code == 200
    assert set(summary_payload.keys()) == expected_keys
    assert summary_payload == overview_payload["metrics"][metric_key]
    assert all(isinstance(value, int) for value in summary_payload.values())


def test_admin_can_list_users_and_change_role_with_audit_log(app, client):
    with app.app_context():
        admin, user, _post, _comment = seed_admin_console_data()
        list_response = client.get("/admin/users?search=console_user", headers=auth_headers(admin.id))
        role_response = client.post(
            f"/admin/users/{user.id}/role",
            json={"role_name": UserRole.MODERATOR, "note": "trusted helper"},
            headers=auth_headers(admin.id),
        )
        audit_response = client.get("/admin/audit-logs", headers=auth_headers(admin.id))
        list_payload = list_response.get_json()
        role_payload = role_response.get_json()
        audit_payload = audit_response.get_json()

    assert list_response.status_code == 200
    assert list_payload["users"][0]["id"] == user.id
    assert role_response.status_code == 200
    assert role_payload["user"]["role_name"] == UserRole.MODERATOR
    assert audit_response.status_code == 200
    assert audit_payload["logs"][0]["action"] == "user.role_update"
    assert audit_payload["logs"][0]["target_type"] == "user"
    assert audit_payload["logs"][0]["target_id"] == user.id


def test_admin_user_management_protects_admin_accounts(app, client):
    with app.app_context():
        admin = create_user("only_admin", UserRole.ADMIN)
        moderator_role = UserRole.query.filter_by(name=UserRole.MODERATOR).first()
        if moderator_role is None:
            moderator_role = UserRole(name=UserRole.MODERATOR, description="moderator role")
            db.session.add(moderator_role)
            db.session.commit()

        demote_response = client.post(
            f"/admin/users/{admin.id}/role",
            json={"role_name": UserRole.MODERATOR},
            headers=auth_headers(admin.id),
        )
        self_delete_response = client.post(
            f"/admin/users/{admin.id}/delete",
            headers=auth_headers(admin.id),
        )

    assert demote_response.status_code == 400
    assert self_delete_response.status_code == 400


def test_admin_can_soft_delete_and_restore_content(app, client):
    with app.app_context():
        admin, _user, post, comment = seed_admin_console_data()
        post_id = post.id
        comment_id = comment.id
        delete_post_response = client.post(
            f"/admin/content/posts/{post_id}/delete",
            json={"note": "duplicate"},
            headers=auth_headers(admin.id),
        )
        public_deleted_post_response = client.get(f"/posts/{post_id}")
        public_deleted_post_comments_response = client.get(f"/comments/post/{post_id}")
        restore_post_response = client.post(
            f"/admin/content/posts/{post_id}/restore",
            headers=auth_headers(admin.id),
        )
        delete_comment_response = client.post(
            f"/admin/content/comments/{comment_id}/delete",
            json={"note": "off topic"},
            headers=auth_headers(admin.id),
        )
        public_post_response = client.get(f"/posts/{post_id}")
        public_comment_response = client.get(f"/comments/{comment_id}")
        summary_response = client.get("/admin/content/summary", headers=auth_headers(admin.id))
        summary_payload = summary_response.get_json()

    assert delete_post_response.status_code == 200
    assert delete_post_response.get_json()["post"]["is_deleted"] is True
    assert public_deleted_post_response.status_code == 404
    assert public_deleted_post_comments_response.status_code == 404
    assert restore_post_response.status_code == 200
    assert restore_post_response.get_json()["post"]["is_deleted"] is False
    assert delete_comment_response.status_code == 200
    assert delete_comment_response.get_json()["comment"]["is_deleted"] is True
    assert public_post_response.status_code == 200
    assert public_post_response.get_json()["comments_list"] == []
    assert public_comment_response.status_code == 404
    assert summary_response.status_code == 200
    assert summary_payload["posts"]["total"] == 1
    assert summary_payload["comments"]["deleted"] == 1


def test_soft_deleted_gugu_parent_is_not_exposed_in_public_reply(app, client):
    with app.app_context():
        admin = create_user("gugu_parent_admin", UserRole.ADMIN)
        user = create_user("gugu_parent_user")
        parent = GuguMessage(author_id=user.id, content="hidden parent")
        db.session.add(parent)
        db.session.flush()
        reply = GuguMessage(
            author_id=user.id,
            content="visible reply",
            reply_to_message_id=parent.id,
        )
        db.session.add(reply)
        db.session.commit()

        delete_parent_response = client.post(
            f"/admin/content/gugu/{parent.id}/delete",
            json={"note": "hide quoted context"},
            headers=auth_headers(admin.id),
        )
        public_messages_response = client.get("/gugu/messages")
        messages = public_messages_response.get_json()["messages"]

    assert delete_parent_response.status_code == 200
    assert public_messages_response.status_code == 200
    assert [message["id"] for message in messages] == [reply.id]
    assert "reply_to" not in messages[0]


def test_admin_can_list_and_soft_delete_gugu_and_files(app, client):
    with app.app_context():
        admin, user, _post, _comment = seed_admin_console_data()
        gugu = GuguMessage.query.filter_by(author_id=user.id).first()
        file_record = File.query.filter_by(user_id=user.id).first()
        gugu_id = gugu.id
        file_id = file_record.id

        gugu_list_response = client.get("/admin/content/gugu", headers=auth_headers(admin.id))
        file_list_response = client.get("/admin/content/files", headers=auth_headers(admin.id))
        delete_gugu_response = client.post(
            f"/admin/content/gugu/{gugu_id}/delete",
            json={"note": "campus moderation"},
            headers=auth_headers(admin.id),
        )
        delete_file_response = client.post(
            f"/admin/content/files/{file_id}/delete",
            json={"note": "invalid upload"},
            headers=auth_headers(admin.id),
        )
        restore_gugu_response = client.post(
            f"/admin/content/gugu/{gugu_id}/restore",
            headers=auth_headers(admin.id),
        )
        audit_count = AdminAuditLog.query.filter(AdminAuditLog.action.in_([
            "content.gugu_delete",
            "content.file_delete",
        ])).count()

    assert gugu_list_response.status_code == 200
    assert gugu_list_response.get_json()["gugu"][0]["id"] == gugu_id
    assert file_list_response.status_code == 200
    assert file_list_response.get_json()["files"][0]["id"] == file_id
    assert delete_gugu_response.status_code == 200
    assert delete_gugu_response.get_json()["gugu"]["is_deleted"] is True
    assert delete_file_response.status_code == 200
    assert delete_file_response.get_json()["file"]["is_deleted"] is True
    assert restore_gugu_response.status_code == 200
    assert restore_gugu_response.get_json()["gugu"]["is_deleted"] is False
    assert audit_count == 2
