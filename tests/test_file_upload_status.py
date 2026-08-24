import pytest
from flask_jwt_extended import create_access_token
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles

from app import create_app
from app.config import Config
from app.extensions import db
from app.models.file import File
from app.models.user import User
from app.models.user_role import UserRole
from app.services.file_service import OSSService


@compiles(JSONB, "sqlite")
def compile_jsonb_sqlite(_type, _compiler, **_kw):
    return "JSON"


class TestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    CACHE_TYPE = "SimpleCache"
    ENABLE_BACKGROUND_TASKS = False
    JWT_SECRET_KEY = "test-secret"


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        role = UserRole.query.filter_by(name="user").first()
        if role is None:
            role = UserRole(name="user", description="Regular user")
            db.session.add(role)
            db.session.flush()
        user = User(
            username="upload-user",
            email="upload-user@hkust-gz.edu.cn",
            email_verified=True,
            role_id=role.id,
        )
        user.set_password("password")
        db.session.add(user)
        db.session.flush()
        pending = File(
            user_id=user.id,
            object_name="user_upload/pending.png",
            original_filename="avatar.png",
            mime_type="image/png",
            file_type=File.AVATAR,
            status="pending",
        )
        uploaded = File(
            user_id=user.id,
            object_name="user_upload/uploaded.png",
            original_filename="uploaded.png",
            mime_type="image/png",
            file_type=File.AVATAR,
            status="uploaded",
        )
        db.session.add_all([pending, uploaded])
        db.session.commit()
        app.config.update(
            USER_ID=user.id,
            PENDING_FILE_ID=pending.id,
            UPLOADED_FILE_ID=uploaded.id,
        )
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def auth(app):
    with app.app_context():
        token = create_access_token(identity=str(app.config["USER_ID"]))
    return {"Authorization": f"Bearer {token}"}


def test_pending_file_stays_pending_when_oss_object_is_absent(client, app, auth, monkeypatch):
    monkeypatch.setattr(OSSService, "object_exists", lambda _name: False)

    response = client.get(f"/files/{app.config['PENDING_FILE_ID']}", headers=auth)

    assert response.status_code == 200
    assert response.get_json()["status"] == "pending"
    with app.app_context():
        assert db.session.get(File, app.config["PENDING_FILE_ID"]).status == "pending"


def test_pending_file_is_not_promoted_by_get_even_if_object_exists(client, app, auth, monkeypatch):
    def fail_if_called(_name):
        raise AssertionError("GET must not verify or promote pending uploads")

    monkeypatch.setattr(OSSService, "object_exists", fail_if_called)

    response = client.get(f"/files/{app.config['PENDING_FILE_ID']}", headers=auth)

    assert response.status_code == 200
    assert response.get_json()["status"] == "pending"
    with app.app_context():
        assert db.session.get(File, app.config["PENDING_FILE_ID"]).status == "pending"


def test_uploaded_file_does_not_query_oss(client, app, auth, monkeypatch):
    def fail_if_called(_name):
        raise AssertionError("uploaded files must not be rechecked")

    monkeypatch.setattr(OSSService, "object_exists", fail_if_called)

    response = client.get(f"/files/{app.config['UPLOADED_FILE_ID']}", headers=auth)

    assert response.status_code == 200
    assert response.get_json()["status"] == "uploaded"
