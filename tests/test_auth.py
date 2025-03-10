# tests/test_auth.py
import pytest
from app import create_app, db


@pytest.fixture
def client():
    app = create_app()
    app.config['TESTING'] = True
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'

    with app.test_client() as client:
        with app.app_context():
            db.create_all()
        yield client


def test_register_and_login(client):
    # Test user registration
    response = client.post('/auth/register', json={
        'username': 'testuser',
        'password': 'testpass',
        'email': 'test@example.com'
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
