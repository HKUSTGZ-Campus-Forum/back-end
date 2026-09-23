import pytest
from flask_jwt_extended import create_access_token
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles

from app import create_app
from app.extensions import db
from app.models.post import Post
from app.models.tag import Tag, TagType
from app.models.user import User
from app.models.user_role import UserRole
from app.routes.post import SYSTEM_ANNOUNCEMENT_TAG


@compiles(JSONB, 'sqlite')
def compile_jsonb_sqlite(_type, _compiler, **_kwargs):
    return 'JSON'


class TestConfig:
    TESTING = True
    SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'
    SQLALCHEMY_ENGINE_OPTIONS = {}
    JWT_SECRET_KEY = 'announcement-test-secret-long-enough'
    CACHE_TYPE = 'SimpleCache'
    AUTO_INIT_ON_STARTUP = False
    ENABLE_BACKGROUND_TASKS = False


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(
        'app.routes.post.content_moderation.moderate_post',
        lambda **_kwargs: {'is_safe': True},
    )
    app = create_app(TestConfig)
    with app.test_client() as test_client:
        with app.app_context():
            db.create_all()
        yield test_client


def create_user(name, role_name):
    role = UserRole.query.filter_by(name=role_name).first()
    if role is None:
        role = UserRole(name=role_name)
        db.session.add(role)
        db.session.flush()
    user = User(username=name, role_id=role.id, password_hash='test-hash')
    db.session.add(user)
    db.session.commit()
    return user


def headers(user_id):
    return {'Authorization': f'Bearer {create_access_token(identity=str(user_id))}'}


def test_only_admin_can_publish_and_manage_announcement(client):
    with client.application.app_context():
        regular = create_user('regular', UserRole.USER)
        publisher = create_user('publisher', UserRole.ADMIN)
        editor = create_user('editor', UserRole.ADMIN)
        regular_headers = headers(regular.id)
        publisher_headers = headers(publisher.id)
        editor_headers = headers(editor.id)

    data = {'title': 'Service update', 'content': 'New course data is ready.', 'tags': [SYSTEM_ANNOUNCEMENT_TAG]}
    denied = client.post('/posts', json=data, headers=regular_headers)
    assert denied.status_code == 403

    created = client.post('/posts', json=data, headers=publisher_headers)
    assert created.status_code == 201
    post_id = created.get_json()['id']

    with client.application.app_context():
        post = db.session.get(Post, post_id)
        assert any(tag.name == SYSTEM_ANNOUNCEMENT_TAG and tag.tag_type.name == TagType.SYSTEM for tag in post.tags)
        assert Tag.query.filter_by(name=SYSTEM_ANNOUNCEMENT_TAG).count() == 1

    listed = client.get('/posts', query_string={'tags': SYSTEM_ANNOUNCEMENT_TAG})
    assert listed.status_code == 200
    assert [post['id'] for post in listed.get_json()['posts']] == [post_id]

    assert client.put(f'/posts/{post_id}', json={'title': 'Changed'}, headers=regular_headers).status_code == 403
    assert client.delete(f'/posts/{post_id}', headers=regular_headers).status_code == 403
    assert client.put(f'/posts/{post_id}', json={'title': 'Updated'}, headers=editor_headers).status_code == 200
    assert client.delete(f'/posts/{post_id}', headers=editor_headers).status_code == 204
