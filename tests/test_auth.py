# tests/test_auth.py
import pytest
from app import create_app, db
from app.models.user_role import UserRole


class TestConfig:
    TESTING = True
    SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'
    SQLALCHEMY_ENGINE_OPTIONS = {}
    JWT_SECRET_KEY = 'test-secret'
    CACHE_TYPE = 'SimpleCache'
    AUTO_INIT_ON_STARTUP = False


@pytest.fixture
def client():
    app = create_app(TestConfig)

    with app.test_client() as client:
        with app.app_context():
            db.create_all()
            db.session.add(UserRole(name=UserRole.USER))
            db.session.commit()
        yield client


def test_register_and_login(client):
    # Test user registration
    response = client.post('/auth/register', json={
        'username': 'testuser',
        'password': 'testpass',
        'email': 'test@connect.hkust-gz.edu.cn'
    })
    assert response.status_code == 201

    # Test user login
    response = client.post('/auth/login', json={
        'username': 'testuser',
        'password': 'testpass'
    })
    data = response.get_json()
    assert response.status_code == 200
    assert 'access_token' in data
