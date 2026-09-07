import hashlib
import hmac
import json
from datetime import timedelta

import pytest
from cryptography.fernet import Fernet
from flask_jwt_extended import create_access_token

from app import create_app
from app.config import Config
from app.extensions import db
from app.models.makerspace import MakerDeployment, MakerSession, MakerSpace, MakerWorker, now
from app.models.user import User
from app.models.user_role import UserRole
from app.services import makerspace_service as service
from app.services.makerspace_proxy import scope_html


class TestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_ENGINE_OPTIONS = {}
    CACHE_TYPE = "SimpleCache"
    AUTO_INIT_ON_STARTUP = False
    ENABLE_BACKGROUND_TASKS = False
    JWT_SECRET_KEY = "makerspace-test-key-not-for-production-123456789"
    MAKERSPACE_ENCRYPTION_KEY = Fernet.generate_key().decode()
    MAKERSPACE_WORKER_TOKEN = "worker-test-token-not-for-production-123456789"
    MAKERSPACE_HOSTING_ENABLED = True


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    for key in ("ALL_PROXY", "all_proxy", "HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"):
        monkeypatch.delenv(key, raising=False)
    instance = create_app(TestConfig)
    with instance.app_context():
        db.create_all()
        for role in (UserRole.USER, UserRole.ADMIN):
            db.session.add(UserRole(name=role, description=role))
        db.session.flush()
        for name, role in (("author", UserRole.USER), ("stranger", UserRole.USER), ("reviewer", UserRole.ADMIN)):
            db.session.add(User(username=name, email=f"{name}@example.edu", email_verified=True, password_hash="disabled", role_id=UserRole.query.filter_by(name=role).one().id))
        db.session.add(MakerWorker(id="school-makerspace", capabilities={"runtime": "runsc"}))
        db.session.commit()
        yield instance
        db.session.remove()
        db.drop_all()


def headers(name="author"):
    user = User.query.filter_by(username=name).one()
    return {"Authorization": "Bearer " + create_access_token(identity=str(user.id))}


def form(slug="campus-tool"):
    return {"slug": slug, "title_zh": "校园工具", "title_en": "Campus tool", "description_zh": "工具说明", "description_en": "A campus tool.", "category": "tools", "repository": "author/private-repo", "branch": "main", "settings": {"runtime": "static", "output_directory": "dist"}, "auto_deploy": True}


def setup_space(client):
    response = client.post("/makerspace", json=form(), headers=headers())
    assert response.status_code == 201, response.json
    response = client.post("/makerspace/campus-tool/credentials", headers=headers())
    assert response.status_code == 200
    return MakerSpace.query.one(), response.json


def ready_build(client):
    space, _ = setup_space(client)
    response = client.post("/makerspace/campus-tool/deployments", headers=headers())
    assert response.status_code == 202, response.json
    item = db.session.get(MakerDeployment, response.json["id"])
    item.status, item.source_sha, item.artifact_digest, item.runtime_port = "ready", "a" * 40, "b" * 64, 20000
    db.session.commit()
    return space, item


def test_private_catalog_and_owner_permissions(app):
    client = app.test_client()
    space, credentials = setup_space(client)
    assert client.get("/makerspace").json == {"spaces": []}
    assert client.get("/makerspace/campus-tool").status_code == 404
    assert client.get("/makerspace/campus-tool", headers=headers("stranger")).status_code == 404
    assert client.put("/makerspace/campus-tool", json=form(), headers=headers("stranger")).status_code == 404
    assert client.get("/makerspace/mine", headers=headers("stranger")).json == {"spaces": []}
    assert credentials["public_key"].startswith("ssh-ed25519 ")
    detail = client.get("/makerspace/campus-tool", headers=headers()).json
    assert "webhook_secret" not in json.dumps(detail)
    assert "PRIVATE KEY" not in json.dumps(detail)
    assert space.encrypted_private_key.startswith("gAAAA")


def test_ownership_and_quota_cannot_be_client_selected(app):
    client = app.test_client()
    for index in range(3):
        data = form(f"tool-{index}") | {"owner_id": User.query.filter_by(username="stranger").one().id, "status": "published", "kind": "external", "external_path": "/admin/"}
        response = client.post("/makerspace", json=data, headers=headers())
        assert response.status_code == 201
        assert response.json["is_owner"] is True
        assert response.json["kind"] == "hosted"
        assert response.json["status"] == "draft"
    assert client.post("/makerspace", json=form("tool-four"), headers=headers()).status_code == 409


def test_review_approves_immutable_artifact_before_separate_public_runtime(app):
    client = app.test_client()
    space, item = ready_build(client)
    path = f"/makerspace/campus-tool/deployments/{item.id}/submit"
    assert client.post(path, headers=headers()).status_code == 200
    review = f"/makerspace/admin/campus-tool/deployments/{item.id}/review"
    data = {"decision": "approve", "source_sha": item.source_sha, "artifact_digest": "c" * 64}
    assert client.post(review, json=data, headers=headers("reviewer")).status_code == 409
    data["artifact_digest"] = item.artifact_digest
    assert client.post(review, json=data, headers=headers("stranger")).status_code == 403
    assert client.post(review, json=data, headers=headers("reviewer")).status_code == 200
    assert space.status == "draft"
    assert item.publication_status == "queued"
    worker = {"Authorization": "Bearer " + TestConfig.MAKERSPACE_WORKER_TOKEN}
    lease = client.post("/makerspace/worker/lease", headers=worker, json={"worker_id": "school-makerspace", "capabilities": {"runtime": "runsc", "disk_quota": True, "network_isolation": True}})
    job = lease.json["job"]
    assert job["kind"] == "publish"
    assert job["private_key"] is None
    receipt = {"lease": job["lease"], "status": "ready", "source_sha": item.source_sha, "artifact_digest": item.artifact_digest, "runtime_port": 20001}
    assert client.post(f"/makerspace/worker/deployments/{item.id}", headers=worker, json=receipt).status_code == 200
    assert space.status == "published"
    assert item.public_runtime_port != item.runtime_port
    space.title_en = "Unreviewed secret title"
    db.session.commit()
    assert client.get("/makerspace?q=secret").json["spaces"] == []
    assert client.get("/makerspace").json["spaces"][0]["title_en"] == "Campus tool"
    assert client.post(f"/makerspace/worker/deployments/{item.id}", headers=worker, json=receipt).status_code == 409


def test_private_launch_is_cookie_bound_and_revoked_with_owner(app):
    client = app.test_client()
    space, item = ready_build(client)
    body = {"deployment_id": item.id}
    assert client.post("/makerspace/campus-tool/launch", json=body, headers=headers("stranger")).status_code == 404
    response = client.post("/makerspace/campus-tool/launch", json=body, headers=headers())
    assert response.status_code == 200
    session = MakerSession.query.one()
    assert app.test_client().get(response.json["url"].removeprefix("/api")).status_code == 404
    cookie = response.headers["Set-Cookie"].split("=", 1)[1].split(";", 1)[0]
    assert service.runtime_session(session.id, cookie)[1].id == item.id
    space.owner.is_deleted = True
    db.session.commit()
    with pytest.raises(service.MakerError):
        service.runtime_session(session.id, cookie)


def test_pushes_are_signed_idempotent_and_coalesced(app):
    client = app.test_client()
    space, credentials = setup_space(client)
    data = {"repository": {"full_name": space.repository}, "ref": "refs/heads/main", "after": "a" * 40}
    raw = json.dumps(data).encode()
    signed = {"Content-Type": "application/json", "X-GitHub-Event": "push", "X-GitHub-Delivery": "delivery-one", "X-Hub-Signature-256": "sha256=" + hmac.new(credentials["webhook_secret"].encode(), raw, hashlib.sha256).hexdigest()}
    path = "/makerspace/hooks/" + space.id
    assert client.post(path, data=raw).status_code == 403
    assert client.post(path, data=raw, headers=signed).status_code == 202
    assert client.post(path, data=raw, headers=signed).json["status"] == "duplicate"
    assert space.pending_source_sha == "a" * 40


def test_secrets_are_snapshotted_and_logs_redacted(app):
    client = app.test_client()
    space, _ = setup_space(client)
    assert client.put("/makerspace/campus-tool/environment", headers=headers(), json={"values": {"SERVICE_SECRET": "private-value"}}).status_code == 200
    item = service.queue_deployment(space, space.owner_id)
    db.session.commit()
    assert json.loads(service.decrypt(item.encrypted_environment)) == {"SERVICE_SECRET": "private-value"}
    assert "private-value" not in json.dumps(service.serialize(space, space.owner, private=True))
    assert "private-value" not in service.redact_log("build failed private-value", ["private-value"])
    assert client.put("/makerspace/campus-tool/environment", headers=headers(), json={"values": {"SERVICE_SECRET": "new"}}).status_code == 409


@pytest.mark.parametrize("settings", [{"directory": "../host"}, {"output_directory": "/etc"}, {"runtime": "host"}, {"runtime": "python", "start_command": ""}])
def test_invalid_runtime_settings_fail_closed(app, settings):
    response = app.test_client().post("/makerspace", headers=headers(), json=form() | {"settings": settings})
    assert response.status_code == 400


def test_html_bridge_keeps_identifier_and_scopes_assets():
    html = scope_html('<html><head><base href="https://evil.example/"></head><script type="module" src="/app.js"></script></html>', "/api/makerspace/run/test/")
    assert "UNIKORN_SPACE_BASE" in html
    assert 'src="/api/makerspace/run/test/app.js"' in html
    assert 'crossorigin="use-credentials"' in html
    assert "evil.example" not in html


def test_bootstrap_ticket_is_single_use_and_logout_revokes_preview(app):
    client = app.test_client()
    space, item = ready_build(client)
    auth = headers()
    response = client.post('/makerspace/campus-tool/launch', json={'deployment_id': item.id}, headers=auth)
    assert 'Partitioned' in response.headers['Set-Cookie']
    session = MakerSession.query.one()
    session.bootstrap_hash = service.hash_value('single-use-ticket')
    session.bootstrap_expires_at = now() + timedelta(seconds=60)
    db.session.commit()
    path = f'/makerspace/run/{session.id}/__unikorn_session__'
    assert client.post(path, json={'ticket': 'wrong'}, headers={'Origin': 'null'}).status_code == 404
    response = client.post(path, json={'ticket': 'single-use-ticket'}, headers={'Origin': 'null'})
    assert response.status_code == 204
    assert response.headers['Access-Control-Allow-Origin'] == 'null'
    assert 'HttpOnly' in response.headers['Set-Cookie'] and 'Partitioned' in response.headers['Set-Cookie']
    assert client.post(path, json={'ticket': 'single-use-ticket'}).status_code == 404
    assert client.post('/auth/logout', headers=auth).status_code == 200
    assert MakerSession.query.count() == 0


def test_admin_cannot_browse_unsubmitted_private_work(app):
    client = app.test_client()
    space, item = ready_build(client)
    assert client.get('/makerspace/campus-tool', headers=headers('reviewer')).status_code == 404
    assert client.post(f'/makerspace/campus-tool/deployments/{item.id}/submit', headers=headers()).status_code == 200
    assert client.get('/makerspace/campus-tool', headers=headers('reviewer')).status_code == 200


def test_admin_author_cannot_approve_their_own_work(app):
    client = app.test_client()
    space, item = ready_build(client)
    space.owner.role_id = UserRole.query.filter_by(name=UserRole.ADMIN).one().id
    db.session.commit()
    assert client.post(f'/makerspace/campus-tool/deployments/{item.id}/submit', headers=headers()).status_code == 200
    result = client.post(f'/makerspace/admin/campus-tool/deployments/{item.id}/review', json={'decision':'approve','source_sha':item.source_sha,'artifact_digest':item.artifact_digest}, headers=headers())
    assert result.status_code == 403 and result.json['code'] == 'independent_review_required'
