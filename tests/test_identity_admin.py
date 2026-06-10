import os

import pytest
from flask_jwt_extended import create_access_token
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles

from app import create_app
from app.config import Config
from app.extensions import db
from app.models.admin_audit_log import AdminAuditLog
from app.models.file import File
from app.models.identity_type import IdentityType
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


def create_identity_type(name: str = "professor") -> IdentityType:
    identity_type = IdentityType(
        name=name,
        display_name=name.title(),
        color="#2563eb",
        icon_name="academic-cap",
        description=f"{name.title()} identity",
        is_active=True,
    )
    db.session.add(identity_type)
    db.session.flush()
    return identity_type


def create_identity(user: User, identity_type: IdentityType, status: str) -> UserIdentity:
    identity = UserIdentity(
        user_id=user.id,
        identity_type_id=identity_type.id,
        status=status,
    )
    db.session.add(identity)
    db.session.flush()
    return identity


def auth_headers(user_id: int) -> dict[str, str]:
    token = create_access_token(identity=str(user_id))
    return {"Authorization": f"Bearer {token}"}


def test_admin_can_list_identity_requests_across_all_statuses(app, client):
    with app.app_context():
        admin = create_user("identity_admin", role_name=UserRole.ADMIN)
        identity_type = create_identity_type()
        pending = create_identity(create_user("pending_user"), identity_type, UserIdentity.PENDING)
        approved = create_identity(create_user("approved_user"), identity_type, UserIdentity.APPROVED)
        rejected = create_identity(create_user("rejected_user"), identity_type, UserIdentity.REJECTED)
        revoked = create_identity(create_user("revoked_user"), identity_type, UserIdentity.REVOKED)
        db.session.commit()

        response = client.get("/identities/admin/requests", headers=auth_headers(admin.id))
        payload = response.get_json()

    assert response.status_code == 200
    assert {item["id"] for item in payload["requests"]} == {
        pending.id,
        approved.id,
        rejected.id,
        revoked.id,
    }
    assert payload["counts"] == {
        "pending": 1,
        "approved": 1,
        "rejected": 1,
        "revoked": 1,
        "total": 4,
    }


def test_admin_can_filter_identity_requests_by_status(app, client):
    with app.app_context():
        admin = create_user("identity_filter_admin", role_name=UserRole.ADMIN)
        identity_type = create_identity_type()
        create_identity(create_user("pending_filter_user"), identity_type, UserIdentity.PENDING)
        approved = create_identity(create_user("approved_filter_user"), identity_type, UserIdentity.APPROVED)
        db.session.commit()

        response = client.get(
            "/identities/admin/requests?status=approved",
            headers=auth_headers(admin.id),
        )
        payload = response.get_json()

    assert response.status_code == 200
    assert [item["id"] for item in payload["requests"]] == [approved.id]
    assert payload["total"] == 1
    assert payload["counts"]["total"] == 2


def test_admin_identity_requests_are_available_under_admin_namespace(app, client):
    with app.app_context():
        admin = create_user("identity_namespace_admin", role_name=UserRole.ADMIN)
        identity_type = create_identity_type()
        pending = create_identity(create_user("namespace_pending_user"), identity_type, UserIdentity.PENDING)
        db.session.commit()

        response = client.get("/admin/identity/requests", headers=auth_headers(admin.id))
        payload = response.get_json()

    assert response.status_code == 200
    assert [item["id"] for item in payload["requests"]] == [pending.id]


def test_admin_identity_list_includes_document_view_metadata(app, client):
    with app.app_context():
        admin = create_user("identity_docs_admin", role_name=UserRole.ADMIN)
        applicant = create_user("identity_docs_user")
        identity_type = create_identity_type()
        file_record = File(
            user_id=applicant.id,
            object_name="identity/docs/proof.pdf",
            original_filename="proof.pdf",
            file_size=12345,
            mime_type="application/pdf",
            status="uploaded",
            file_type=File.IDENTITY_DOCUMENT,
            entity_type="identity_verification",
        )
        db.session.add(file_record)
        db.session.flush()
        uploaded_at = file_record.created_at.isoformat()
        identity = UserIdentity(
            user_id=applicant.id,
            identity_type_id=identity_type.id,
            status=UserIdentity.PENDING,
            verification_documents=[{
                "file_id": file_record.id,
                "filename": file_record.original_filename,
                "uploaded_at": uploaded_at,
            }],
        )
        db.session.add(identity)
        db.session.commit()

        response = client.get("/identities/admin/requests", headers=auth_headers(admin.id))
        payload = response.get_json()

    assert response.status_code == 200
    documents = payload["requests"][0]["verification_documents"]
    assert documents == [{
        "file_id": file_record.id,
        "filename": "proof.pdf",
        "uploaded_at": uploaded_at,
        "size": 12345,
        "mime_type": "application/pdf",
        "view_url": f"/api/files/view/{file_record.id}",
    }]


def test_non_admin_cannot_list_identity_requests(app, client):
    with app.app_context():
        user = create_user("identity_regular_user")
        response = client.get("/identities/admin/requests", headers=auth_headers(user.id))

    assert response.status_code == 403


def test_admin_identity_actions_write_unified_audit_logs(app, client):
    with app.app_context():
        admin = create_user("identity_action_admin", role_name=UserRole.ADMIN)
        identity_type = create_identity_type()
        identity = create_identity(create_user("identity_action_user"), identity_type, UserIdentity.PENDING)
        db.session.commit()

        approve_response = client.post(
            f"/admin/identity/{identity.id}/approve",
            json={"notes": "verified from campus document"},
            headers=auth_headers(admin.id),
        )
        audit_log = AdminAuditLog.query.filter_by(
            action="identity.approve",
            target_id=identity.id,
        ).first()

    assert approve_response.status_code == 200
    assert approve_response.get_json()["verification"]["status"] == UserIdentity.APPROVED
    assert audit_log is not None
    assert audit_log.metadata_json["user_id"] == identity.user_id
