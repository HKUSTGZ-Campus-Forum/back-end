import pytest
from flask import Response
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles

from app import create_app
from app.config import Config
from app.extensions import db
from app.models.file import File
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
def app():
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


def create_user_with_avatar(*, status="uploaded", file_type=File.AVATAR):
    role = UserRole(name=UserRole.USER, description="user")
    db.session.add(role)
    db.session.flush()

    user = User(
        username="avatar_user",
        email="avatar_user@connect.hkust-gz.edu.cn",
        email_verified=True,
        role_id=role.id,
    )
    user.password_hash = "test-password-hash"
    db.session.add(user)
    db.session.flush()

    avatar = File(
        user_id=user.id,
        object_name="avatars/avatar.jpeg",
        original_filename="avatar.jpeg",
        file_size=128,
        mime_type="image/jpeg",
        status=status,
        file_type=file_type,
    )
    db.session.add(avatar)
    db.session.flush()
    user.profile_picture_file_id = avatar.id
    db.session.commit()
    return user, avatar


def test_public_user_returns_database_derived_same_origin_avatar(app, client):
    with app.app_context():
        user, avatar = create_user_with_avatar()
        response = client.get(f"/users/public/{user.id}")

    assert response.status_code == 200
    assert response.get_json()["profile_picture_url"] == f"/api/files/avatar/{avatar.id}"


def test_public_user_returns_no_avatar_when_database_has_no_current_avatar(app, client):
    with app.app_context():
        role = UserRole(name=UserRole.USER, description="user")
        db.session.add(role)
        db.session.flush()
        user = User(username="no_avatar", role_id=role.id)
        user.password_hash = "test-password-hash"
        db.session.add(user)
        db.session.commit()
        response = client.get(f"/users/public/{user.id}")

    assert response.status_code == 200
    assert response.get_json()["profile_picture_url"] is None


def test_avatar_endpoint_streams_only_the_users_current_avatar(app, client, monkeypatch):
    with app.app_context():
        user, avatar = create_user_with_avatar()
        avatar_id = avatar.id
        streamed = []

        def fake_stream(file_record, cache_control):
            streamed.append((file_record.id, cache_control))
            return Response(b"avatar-bytes", mimetype="image/jpeg")

        monkeypatch.setattr("app.routes.file._stream_file_from_oss", fake_stream)
        response = client.get(f"/files/avatar/{avatar_id}")

        other = File(
            user_id=user.id,
            object_name="avatars/other.jpeg",
            original_filename="other.jpeg",
            mime_type="image/jpeg",
            status="uploaded",
            file_type=File.AVATAR,
        )
        db.session.add(other)
        db.session.commit()
        other_response = client.get(f"/files/avatar/{other.id}")

    assert response.status_code == 200
    assert response.data == b"avatar-bytes"
    assert streamed == [(avatar_id, "public, max-age=86400, immutable")]
    assert other_response.status_code == 404


@pytest.mark.parametrize("status,file_type", [
    ("pending", File.AVATAR),
    ("uploaded", File.GENERAL),
])
def test_invalid_avatar_records_fall_back_to_no_avatar(app, client, status, file_type):
    with app.app_context():
        user, avatar = create_user_with_avatar(status=status, file_type=file_type)
        public_response = client.get(f"/users/public/{user.id}")
        file_response = client.get(f"/files/avatar/{avatar.id}")

    assert public_response.get_json()["profile_picture_url"] is None
    assert file_response.status_code == 404
