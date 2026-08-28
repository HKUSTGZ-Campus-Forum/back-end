import os

import pytest
from flask import Response
from flask_jwt_extended import create_access_token
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles

from app import create_app
from app.config import Config
from app.extensions import db
from app.models.admin_audit_log import AdminAuditLog
from app.models.file import File
from app.models.home_carousel_slide import HomeCarouselSlide
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
    for proxy_key in ["ALL_PROXY", "all_proxy", "HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"]:
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


def create_user(username, role_name=UserRole.USER):
    role = UserRole.query.filter_by(name=role_name).first()
    if role is None:
        role = UserRole(name=role_name, description=f"{role_name} role")
        db.session.add(role)
        db.session.flush()
    user = User(
        username=username,
        email=f"{username}@connect.hkust-gz.edu.cn",
        email_verified=True,
        role_id=role.id,
        password_hash="disabled",
    )
    db.session.add(user)
    db.session.flush()
    return user


def auth_headers(user_id):
    token = create_access_token(identity=str(user_id))
    return {"Authorization": f"Bearer {token}"}


def static_slide(locale, path, order, active=True):
    return HomeCarouselSlide(
        locale=locale,
        image_path=path,
        alt_text_zh="中文替代文本" if locale != "en" else None,
        alt_text_en="English alternative text" if locale != "zh" else None,
        sort_order=order,
        is_active=active,
    )


def test_public_carousel_filters_locale_active_and_archived_slides(app, client):
    with app.app_context():
        db.session.add_all([
            static_slide("all", "/both.jpg", 10),
            static_slide("zh", "/zh.jpg", 20),
            static_slide("en", "/en.jpg", 30),
            static_slide("zh", "/inactive.jpg", 40, active=False),
            static_slide("zh", "/archived.jpg", 50),
        ])
        db.session.flush()
        HomeCarouselSlide.query.filter_by(image_path="/archived.jpg").one().is_deleted = True
        db.session.commit()
        zh_response = client.get("/home/carousel?locale=zh")
        en_response = client.get("/home/carousel?locale=en")

    assert zh_response.status_code == 200
    assert [slide["image_url"] for slide in zh_response.get_json()["slides"]] == ["/both.jpg", "/zh.jpg"]
    assert zh_response.get_json()["slides"][0]["alt_text"] == "中文替代文本"
    assert en_response.status_code == 200
    assert [slide["image_url"] for slide in en_response.get_json()["slides"]] == ["/both.jpg", "/en.jpg"]
    assert en_response.headers["Cache-Control"].startswith("public, max-age=60")


def test_non_admin_cannot_manage_or_upload_carousel_images(app, client):
    with app.app_context():
        user = create_user("carousel_regular")
        db.session.commit()
        list_response = client.get("/admin/carousel", headers=auth_headers(user.id))
        upload_response = client.post(
            "/files/upload",
            json={
                "filename": "banner.jpg",
                "file_type": File.CAROUSEL_IMAGE,
                "entity_type": "home_carousel",
                "content_type": "image/jpeg",
                "file_size": 1024,
            },
            headers=auth_headers(user.id),
        )

    assert list_response.status_code == 403
    assert upload_response.status_code == 403


def test_admin_carousel_lifecycle_reorders_and_audits(app, client):
    with app.app_context():
        admin = create_user("carousel_admin", UserRole.ADMIN)
        first_file = File(
            user_id=admin.id,
            object_name="carousel/first.jpg",
            original_filename="first.jpg",
            mime_type="image/jpeg",
            status="uploaded",
            file_type=File.CAROUSEL_IMAGE,
            entity_type="home_carousel",
        )
        second_file = File(
            user_id=admin.id,
            object_name="carousel/second.jpg",
            original_filename="second.jpg",
            mime_type="image/jpeg",
            status="uploaded",
            file_type=File.CAROUSEL_IMAGE,
            entity_type="home_carousel",
        )
        db.session.add_all([first_file, second_file])
        db.session.commit()

        first_response = client.post(
            "/admin/carousel",
            json={
                "locale": "zh",
                "image_file_id": first_file.id,
                "alt_text_zh": "中文首页横幅",
                "href": "/courses",
            },
            headers=auth_headers(admin.id),
        )
        second_response = client.post(
            "/admin/carousel",
            json={
                "locale": "en",
                "image_file_id": second_file.id,
                "alt_text_en": "English home banner",
                "href": "https://example.edu/path",
            },
            headers=auth_headers(admin.id),
        )
        first_id = first_response.get_json()["slide"]["id"]
        second_id = second_response.get_json()["slide"]["id"]
        update_response = client.patch(
            f"/admin/carousel/{first_id}",
            json={"is_active": False, "href": None},
            headers=auth_headers(admin.id),
        )
        reorder_response = client.post(
            "/admin/carousel/reorder",
            json={"ordered_ids": [second_id, first_id]},
            headers=auth_headers(admin.id),
        )
        archive_response = client.post(
            f"/admin/carousel/{first_id}/archive",
            headers=auth_headers(admin.id),
        )
        restore_response = client.post(
            f"/admin/carousel/{first_id}/restore",
            headers=auth_headers(admin.id),
        )
        audit_actions = {
            log.action for log in AdminAuditLog.query.filter_by(target_type="home_carousel_slide").all()
        }
        linked_file = db.session.get(File, first_file.id)

    assert first_response.status_code == 201
    assert second_response.status_code == 201
    assert linked_file.entity_id == first_id
    assert update_response.status_code == 200
    assert update_response.get_json()["slide"]["is_active"] is False
    assert update_response.get_json()["slide"]["href"] is None
    assert [slide["id"] for slide in reorder_response.get_json()["slides"]] == [second_id, first_id]
    assert archive_response.get_json()["slide"]["is_deleted"] is True
    assert restore_response.get_json()["slide"]["is_deleted"] is False
    assert {"carousel.create", "carousel.update", "carousel.reorder", "carousel.archive", "carousel.restore"} <= audit_actions


@pytest.mark.parametrize(
    "href",
    [
        "javascript:alert(1)",
        "http://example.com",
        "//evil.example",
        "/\\evil",
        "/path with space",
        "https://user:secret@example.com/path",
        "https://[invalid",
    ],
)
def test_admin_carousel_rejects_unsafe_destinations(app, client, href):
    with app.app_context():
        admin = create_user("carousel_validation_admin", UserRole.ADMIN)
        image = File(
            user_id=admin.id,
            object_name="carousel/validation.jpg",
            original_filename="validation.jpg",
            mime_type="image/jpeg",
            status="uploaded",
            file_type=File.CAROUSEL_IMAGE,
            entity_type="home_carousel",
        )
        db.session.add(image)
        db.session.commit()
        response = client.post(
            "/admin/carousel",
            json={
                "locale": "all",
                "image_file_id": image.id,
                "alt_text_zh": "中文",
                "alt_text_en": "English",
                "href": href,
            },
            headers=auth_headers(admin.id),
        )

    assert response.status_code == 400


def test_public_carousel_file_requires_current_slide_link(app, client, monkeypatch):
    with app.app_context():
        admin = create_user("carousel_file_admin", UserRole.ADMIN)
        image = File(
            user_id=admin.id,
            object_name="carousel/public.jpg",
            original_filename="public.jpg",
            mime_type="image/jpeg",
            status="uploaded",
            file_type=File.CAROUSEL_IMAGE,
            entity_type="home_carousel",
        )
        db.session.add(image)
        db.session.flush()
        slide = HomeCarouselSlide(
            locale="all",
            image_file_id=image.id,
            alt_text_zh="中文",
            alt_text_en="English",
            sort_order=10,
        )
        db.session.add(slide)
        db.session.flush()
        image.entity_id = slide.id
        db.session.commit()

        import app.routes.file as file_routes

        monkeypatch.setattr(
            file_routes,
            "_stream_file_from_oss",
            lambda _record, cache_control: Response(cache_control, status=200),
        )
        allowed_response = client.get(f"/files/view/{image.id}")
        slide.is_deleted = True
        db.session.commit()
        archived_response = client.get(f"/files/view/{image.id}")

    assert allowed_response.status_code == 200
    assert allowed_response.get_data(as_text=True) == "public, max-age=3600"
    assert archived_response.status_code == 404
