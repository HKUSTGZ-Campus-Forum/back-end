import pytest
from flask_jwt_extended import create_access_token
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles

from app import create_app
from app.config import Config
from app.extensions import db
from app.models.course import Course
from app.models.user import User
from app.models.user_role import UserRole


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


def _create_admin() -> User:
    role = UserRole.query.filter_by(name=UserRole.ADMIN).first()
    if role is None:
        role = UserRole(name=UserRole.ADMIN, description="Admin role")
        db.session.add(role)
        db.session.flush()
    admin = User(
        username="course_catalog_admin",
        email="course_catalog_admin@connect.hkust-gz.edu.cn",
        role_id=role.id,
        email_verified=True,
        password_hash="test-password-hash",
    )
    db.session.add(admin)
    db.session.flush()
    return admin


def _auth_headers(user_id: int) -> dict[str, str]:
    token = create_access_token(identity=str(user_id))
    return {"Authorization": f"Bearer {token}"}


def test_sync_course_catalog_upserts_course_rows(app):
    from app.services.course_catalog_sync import sync_course_catalog_from_payload

    payload = {
        "courses": [
            {
                "course_code": "AIAA1010",
                "course_title": "Academic Orientation for AI Students",
                "credit": "1",
                "course_desc": "Official description.",
            }
        ]
    }

    with app.app_context():
        result = sync_course_catalog_from_payload(payload)
        course = Course.query.filter_by(code="AIAA1010").one()

    assert result["upserted"] == 1
    assert course.name == "Academic Orientation for AI Students"
    assert course.credits == 1
    assert course.description == "Official description."
    assert course.normalized_code == "AIAA1010"


def test_sync_course_catalog_updates_existing_spaced_course_code(app):
    from app.services.course_catalog_sync import sync_course_catalog_from_payload

    payload = {
        "courses": [
            {
                "course_code": "AIAA1010",
                "course_title": "Academic Orientation for AI Students",
                "credit": "1",
                "course_desc": "Official description.",
            }
        ]
    }

    with app.app_context():
        db.session.add(Course(code="AIAA 1010", name="Old title", credits=0, is_active=True, is_deleted=False))
        db.session.commit()
        before_count = Course.query.count()

        result = sync_course_catalog_from_payload(payload)
        after_count = Course.query.count()
        spaced_course = Course.query.filter_by(code="AIAA 1010").one()
        catalog_course = Course.query.filter_by(code="AIAA1010").one()

    assert result["upserted"] == 1
    assert after_count == before_count
    assert spaced_course.name == "Academic Orientation for AI Students"
    assert spaced_course.credits == 1
    assert spaced_course.description == "Official description."
    assert catalog_course.description == "Official description."


def test_sync_course_catalog_parses_credit_ranges_for_new_courses(app):
    from app.services.course_catalog_sync import sync_course_catalog_from_payload

    payload = {
        "courses": [
            {
                "course_code": "TEST4019",
                "course_title": "Special Topics",
                "credit": "3-4",
                "course_desc": "Range credit description.",
            }
        ]
    }

    with app.app_context():
        result = sync_course_catalog_from_payload(payload)
        course = Course.query.filter_by(code="TEST4019").one()

    assert result["upserted"] == 1
    assert course.credits == 3
    assert course.description == "Range credit description."


def test_sync_course_catalog_keeps_explicit_zero_credit_courses(app):
    from app.services.course_catalog_sync import sync_course_catalog_from_payload

    payload = {
        "courses": [
            {
                "course_code": "TEST0000",
                "course_title": "Zero Credit Seminar",
                "credit": "0",
                "course_desc": "A valid zero-credit course.",
            }
        ]
    }

    with app.app_context():
        result = sync_course_catalog_from_payload(payload)
        course = Course.query.filter_by(code="TEST0000").one()

    assert result["upserted"] == 1
    assert result["skipped"] == 0
    assert course.credits == 0


def test_sync_course_catalog_updates_course_rules(app):
    from app.services.course_catalog_sync import sync_course_catalog_from_payload

    payload = {
        "courses": [
            {
                "course_code": "RULE1504",
                "course_title": "Honors General Physics II",
                "credit": "3",
                "course_desc": "Official catalog description.",
                "pre_requirement": "(UFUG 1501 or UFUG 1503) AND (UFUG 1102 or UFUG 1105)",
                "co_requirement": None,
                "exclusion": "UFUG 1502",
                "subject": "RULE",
                "catalog_number": "1504",
            }
        ]
    }

    with app.app_context():
        db.session.add(Course(
            code="RULE1504",
            name="Honors General Physics II",
            credits=3,
            is_active=True,
            is_deleted=False,
        ))
        db.session.commit()

        result = sync_course_catalog_from_payload(payload)
        course = Course.query.filter_by(code="RULE1504").one()

    assert result["upserted"] == 1
    assert course.pre_requirement == "(UFUG 1501 or UFUG 1503) AND (UFUG 1102 or UFUG 1105)"
    assert course.co_requirement is None
    assert course.exclusion == "UFUG 1502"
    assert course.subject == "RULE"
    assert course.catalog_number == "1504"


def test_sync_uses_stored_normalized_identity_without_rewriting_legacy_code(app):
    from app.services.course_catalog_sync import sync_course_catalog_from_payload

    payload = {
        "courses": [
            {
                "course_code": "NORM9001",
                "course_title": "Normalized Identity",
                "credit": "3",
            }
        ]
    }

    with app.app_context():
        legacy = Course(
            code="LEGACY-9001",
            normalized_code="NORM9001",
            name="Old title",
            credits=3,
        )
        db.session.add(legacy)
        db.session.commit()
        before_count = Course.query.count()

        sync_course_catalog_from_payload(payload)

        assert Course.query.count() == before_count
        assert legacy.code == "LEGACY-9001"
        assert legacy.name == "Normalized Identity"


def test_sync_does_not_normalize_ambiguous_legacy_pairs(app):
    from app.services.course_catalog_sync import sync_course_catalog_from_payload

    payload = {
        "courses": [
            {
                "course_code": "SAFE9001",
                "course_title": "Safe Startup",
                "credit": "3",
            }
        ]
    }

    with app.app_context():
        canonical = Course(
            code="SAFE9001",
            normalized_code="SAFE9001",
            name="Old canonical title",
            credits=3,
        )
        legacy = Course(code="SAFE 9001", name="Old legacy title", credits=3)
        db.session.add_all([canonical, legacy])
        db.session.commit()

        sync_course_catalog_from_payload(payload)

        assert canonical.normalized_code == "SAFE9001"
        assert legacy.normalized_code is None
        assert canonical.name == "Safe Startup"
        assert legacy.name == "Safe Startup"


def test_admin_create_populates_normalized_code_and_rejects_whitespace_alias(app, client):
    with app.app_context():
        admin = _create_admin()
        db.session.commit()
        headers = _auth_headers(admin.id)

        created = client.post(
            "/courses",
            headers=headers,
            json={
                "code": "NADM 9001",
                "name": "Admin-created Course",
                "instructor_id": admin.id,
                "credits": 3,
            },
        )
        duplicate = client.post(
            "/courses",
            headers=headers,
            json={
                "code": "NADM9001",
                "name": "Duplicate Course",
                "instructor_id": admin.id,
                "credits": 3,
            },
        )

        course = Course.query.filter_by(code="NADM 9001").one()
        assert created.status_code == 201
        assert course.normalized_code == "NADM9001"
        assert duplicate.status_code == 400
        assert Course.query.filter(
            Course.normalized_code == "NADM9001",
            Course.is_deleted == False,
        ).count() == 1


def test_admin_create_rejects_legacy_alias_without_backfilling_it(app, client):
    with app.app_context():
        admin = _create_admin()
        legacy = Course(code="LADM 9001", name="Legacy Course", credits=3)
        db.session.add(legacy)
        db.session.commit()
        headers = _auth_headers(admin.id)

        response = client.post(
            "/courses",
            headers=headers,
            json={
                "code": "LADM9001",
                "name": "Duplicate Course",
                "instructor_id": admin.id,
                "credits": 3,
            },
        )

        assert response.status_code == 400
        assert legacy.normalized_code is None
        assert Course.query.filter(
            Course.is_deleted == False,
        ).filter(Course.code.in_(["LADM 9001", "LADM9001"])).count() == 1


def test_admin_update_rejects_normalized_alias_and_leaves_course_unchanged(app, client):
    with app.app_context():
        admin = _create_admin()
        source = Course(
            code="UADM9001",
            normalized_code="UADM9001",
            name="Source Course",
            credits=3,
        )
        target = Course(
            code="UADM9002",
            normalized_code="UADM9002",
            name="Target Course",
            credits=3,
        )
        db.session.add_all([admin, source, target])
        db.session.commit()
        headers = _auth_headers(admin.id)

        response = client.put(
            f"/courses/{target.id}",
            headers=headers,
            json={"code": "UADM 9001"},
        )

        db.session.refresh(target)
        assert response.status_code == 400
        assert target.code == "UADM9002"
        assert target.normalized_code == "UADM9002"


def test_admin_update_sets_normalized_identity_for_unique_code(app, client):
    with app.app_context():
        admin = _create_admin()
        course = Course(code="OLD9001", name="Old Course", credits=3)
        db.session.add_all([admin, course])
        db.session.commit()
        headers = _auth_headers(admin.id)

        response = client.put(
            f"/courses/{course.id}",
            headers=headers,
            json={"code": "NEWC 9001"},
        )

        db.session.refresh(course)
        assert response.status_code == 200
        assert course.code == "NEWC 9001"
        assert course.normalized_code == "NEWC9001"


@pytest.mark.parametrize("method", ["post", "put"])
def test_admin_course_write_rejects_whitespace_only_code(app, client, method):
    with app.app_context():
        admin = _create_admin()
        course = Course(code="KEEP9001", name="Keep Course", credits=3)
        db.session.add_all([admin, course])
        db.session.commit()
        headers = _auth_headers(admin.id)

        if method == "post":
            response = client.post(
                "/courses",
                headers=headers,
                json={
                    "code": "   ",
                    "name": "Invalid Course",
                    "instructor_id": admin.id,
                    "credits": 3,
                },
            )
        else:
            response = client.put(
                f"/courses/{course.id}",
                headers=headers,
                json={"code": "   "},
            )

        db.session.refresh(course)
        assert response.status_code == 400
        assert course.code == "KEEP9001"
        assert course.normalized_code is None
