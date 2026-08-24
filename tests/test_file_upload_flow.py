from types import SimpleNamespace

import pytest
from flask_jwt_extended import create_access_token

from app import create_app
from app.extensions import db
from app.models.file import File
from app.models.post import Post
from app.models.user import User
from app.models.user_role import UserRole
from app.services.file_service import OSSService
from app.routes.file import _stream_file_from_oss


class TestConfig:
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_ENGINE_OPTIONS = {}
    JWT_SECRET_KEY = "test-secret"
    CACHE_TYPE = "SimpleCache"
    AUTO_INIT_ON_STARTUP = False
    ENABLE_BACKGROUND_TASKS = False


class FakeBucket:
    def __init__(self, size=128, mime_type="image/png"):
        self.size = size
        self.mime_type = mime_type
        self.deleted = []

    def head_object(self, _object_name):
        return SimpleNamespace(
            content_length=self.size,
            content_type=self.mime_type,
            headers={"Content-Type": self.mime_type},
        )

    def delete_object(self, object_name):
        self.deleted.append(object_name)


@pytest.fixture
def upload_client(monkeypatch):
    app = create_app(TestConfig)
    fake_bucket = FakeBucket()
    monkeypatch.setattr(OSSService, "_create_upload_bucket", staticmethod(lambda: fake_bucket))

    with app.test_client() as client:
        with app.app_context():
            db.create_all()
            role = UserRole(name=UserRole.USER)
            db.session.add(role)
            db.session.flush()
            user = User(username="uploader", email="uploader@example.com", role_id=role.id)
            user.set_password("password")
            db.session.add(user)
            db.session.commit()
            token = create_access_token(identity=str(user.id))
            user_id = user.id
        yield client, {"Authorization": f"Bearer {token}"}, user_id, fake_bucket


def _pending_file(user_id, *, file_type=File.POST_IMAGE, mime_type="image/png"):
    record = File(
        user_id=user_id,
        object_name=f"user_upload/{user_id}/test-object",
        original_filename="test.png",
        status="pending",
        file_type=file_type,
        entity_type="post",
        mime_type=mime_type,
    )
    db.session.add(record)
    db.session.commit()
    return record.id


def test_get_file_does_not_promote_pending_upload(upload_client):
    client, headers, user_id, _bucket = upload_client
    with client.application.app_context():
        file_id = _pending_file(user_id)

    response = client.get(f"/files/{file_id}", headers=headers)

    assert response.status_code == 200
    assert response.get_json()["status"] == "pending"
    with client.application.app_context():
        assert db.session.get(File, file_id).status == "pending"


def test_complete_upload_uses_verified_oss_metadata(upload_client):
    client, headers, user_id, bucket = upload_client
    bucket.size = 4321
    bucket.mime_type = "image/webp"
    with client.application.app_context():
        file_id = _pending_file(user_id)

    response = client.post(f"/files/{file_id}/complete", headers=headers)

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "uploaded"
    assert payload["file_size"] == 4321
    assert payload["mime_type"] == "image/webp"


def test_complete_upload_rejects_oversize_video(upload_client):
    client, headers, user_id, bucket = upload_client
    bucket.size = File.MAX_VIDEO_UPLOAD_BYTES + 1
    bucket.mime_type = "video/mp4"
    with client.application.app_context():
        file_id = _pending_file(
            user_id,
            file_type=File.POST_ATTACHMENT,
            mime_type="video/mp4",
        )

    response = client.post(f"/files/{file_id}/complete", headers=headers)

    assert response.status_code == 422
    assert "maximum size" in response.get_json()["message"]
    with client.application.app_context():
        assert db.session.get(File, file_id).status == "error"
    assert f"user_upload/{user_id}/test-object" in bucket.deleted


def test_public_serializer_does_not_expose_storage_fields(upload_client):
    _client, _headers, user_id, _bucket = upload_client
    with _client.application.app_context():
        file_id = _pending_file(user_id)
        record = db.session.get(File, file_id)
        record.status = "uploaded"
        record.entity_id = 42
        payload = record.to_dict()

    assert "object_name" not in payload
    assert "user_id" not in payload
    assert "url" not in payload
    assert payload["view_url"] == f"/api/files/view/{file_id}"


def test_storage_proxy_forwards_video_range_requests(upload_client, monkeypatch):
    client, _headers, _user_id, _bucket = upload_client
    observed = {}

    class FakeResponse:
        status_code = 206
        headers = {
            "Content-Type": "video/mp4",
            "Content-Length": "4",
            "Content-Range": "bytes 0-3/100",
            "Accept-Ranges": "bytes",
        }

        def iter_content(self, chunk_size):
            assert chunk_size == 8192
            yield b"test"

        def close(self):
            observed["closed"] = True

    def fake_get(url, *, headers, stream, timeout):
        observed.update(url=url, headers=headers, stream=stream, timeout=timeout)
        return FakeResponse()

    monkeypatch.setattr("requests.get", fake_get)
    record = SimpleNamespace(
        id=9,
        url="https://storage.example/video",
        mime_type="video/mp4",
        original_filename="clip.mp4",
    )

    with client.application.test_request_context(
        "/files/view/9",
        headers={"Range": "bytes=0-3"},
    ):
        response = _stream_file_from_oss(record)
        assert response.status_code == 206
        assert response.headers["Content-Range"] == "bytes 0-3/100"
        assert response.get_data() == b"test"

    assert observed["headers"] == {"Range": "bytes=0-3"}
    assert observed["closed"] is True


def test_post_attachment_binding_is_atomic(upload_client, monkeypatch):
    client, headers, user_id, _bucket = upload_client
    monkeypatch.setattr(
        "app.routes.post.content_moderation.moderate_post",
        lambda **_kwargs: {"is_safe": True, "reason": "", "risk_level": "low"},
    )
    with client.application.app_context():
        file_id = _pending_file(user_id)
        record = db.session.get(File, file_id)
        record.status = "uploaded"
        record.file_size = 128
        db.session.commit()

    response = client.post(
        "/posts",
        headers=headers,
        json={
            "title": "Atomic attachment validation",
            "content": "## Markdown body\n\nEnough content.",
            "file_ids": [file_id, 999999],
        },
    )

    assert response.status_code == 400
    with client.application.app_context():
        assert Post.query.count() == 0
        assert db.session.get(File, file_id).entity_id is None


def test_post_binds_only_verified_attachment_set(upload_client, monkeypatch):
    client, headers, user_id, _bucket = upload_client
    monkeypatch.setattr(
        "app.routes.post.content_moderation.moderate_post",
        lambda **_kwargs: {"is_safe": True, "reason": "", "risk_level": "low"},
    )
    with client.application.app_context():
        file_id = _pending_file(user_id)
        record = db.session.get(File, file_id)
        record.status = "uploaded"
        record.file_size = 128
        db.session.commit()

    markdown = "## Markdown body\n\n- one\n- two"
    response = client.post(
        "/posts",
        headers=headers,
        json={"title": "Verified attachment post", "content": markdown, "file_ids": [file_id]},
    )

    assert response.status_code == 201
    with client.application.app_context():
        post = Post.query.one()
        record = db.session.get(File, file_id)
        assert post.content == markdown
        assert record.entity_type == "post"
        assert record.entity_id == post.id
