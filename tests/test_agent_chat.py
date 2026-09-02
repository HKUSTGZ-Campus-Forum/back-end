from types import SimpleNamespace

import pytest
from flask_jwt_extended import create_access_token

from app import create_app
from app.extensions import db
from app.models import AgentConversation, AgentMessage, User, UserRole
from app.services.agent_chat_service import agent_chat_service


class TestConfig:
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_ENGINE_OPTIONS = {}
    JWT_SECRET_KEY = "agent-test-secret"
    CACHE_TYPE = "SimpleCache"
    AUTO_INIT_ON_STARTUP = False
    ENABLE_BACKGROUND_TASKS = False
    AGENT_ENABLED = True
    AGENT_BASE_URL = "https://sub2api.example/v1"
    AGENT_API_KEY = "test-key"
    AGENT_MODEL = "test-model"
    AGENT_MAX_MESSAGE_CHARS = 4000
    AGENT_CONTEXT_MESSAGES = 20
    AGENT_REQUESTS_PER_MINUTE = 0


@pytest.fixture()
def app():
    application = create_app(TestConfig)
    with application.app_context():
        db.create_all()
        role = UserRole(name=UserRole.USER)
        db.session.add(role)
        db.session.flush()
        users = [
            User(
                username="agent-user",
                email="agent-user@example.com",
                password_hash="disabled",
                role_id=role.id,
            ),
            User(
                username="other-user",
                email="other-user@example.com",
                password_hash="disabled",
                role_id=role.id,
            ),
        ]
        db.session.add_all(users)
        db.session.commit()
        application.config["TEST_USER_IDS"] = [user.id for user in users]
    yield application


@pytest.fixture()
def client(app):
    return app.test_client()


def auth_headers(app, user_index=0):
    with app.app_context():
        token = create_access_token(
            identity=str(app.config["TEST_USER_IDS"][user_index])
        )
    return {"Authorization": f"Bearer {token}"}


def fake_reply(messages):
    return {
        "content": f"Answer: {messages[-1]['content']}",
        "input_tokens": 12,
        "output_tokens": 7,
    }


def test_agent_service_calls_openai_compatible_provider(app, monkeypatch):
    captured = {}

    class FakeCompletions:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content="可以回答网站问题")
                    )
                ],
                usage=SimpleNamespace(prompt_tokens=11, completion_tokens=6),
            )

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=FakeCompletions())
    )
    monkeypatch.setattr(agent_chat_service, "_get_client", lambda: fake_client)

    with app.app_context():
        result = agent_chat_service.create_reply(
            [{"role": "user", "content": "课程探索在哪里？"}]
        )

    assert captured["model"] == "test-model"
    assert captured["messages"][0]["role"] == "system"
    assert captured["messages"][-1] == {
        "role": "user",
        "content": "课程探索在哪里？",
    }
    assert result == {
        "content": "可以回答网站问题",
        "input_tokens": 11,
        "output_tokens": 6,
    }


def test_agent_routes_require_authentication(client):
    assert client.get("/agent/status").status_code == 401
    assert client.get("/agent/conversations").status_code == 401
    assert client.post("/agent/chat", json={"message": "hello"}).status_code == 401


def test_chat_creates_persistent_conversation_and_history(app, client, monkeypatch):
    seen_contexts = []

    def reply(messages):
        seen_contexts.append(messages)
        return fake_reply(messages)

    monkeypatch.setattr(agent_chat_service, "create_reply", reply)
    headers = auth_headers(app)

    first = client.post(
        "/agent/chat",
        json={"message": "Where is course exploration?"},
        headers=headers,
    )
    assert first.status_code == 201
    first_payload = first.get_json()
    conversation_id = first_payload["conversation"]["id"]
    assert first_payload["conversation"]["title"] == "Where is course exploration?"
    assert first_payload["assistant_message"]["content"].startswith("Answer:")
    assert first.headers["Cache-Control"] == "private, no-store"

    second = client.post(
        "/agent/chat",
        json={"conversation_id": conversation_id, "message": "And saved plans?"},
        headers=headers,
    )
    assert second.status_code == 201
    assert [item["role"] for item in seen_contexts[-1]] == [
        "user",
        "assistant",
        "user",
    ]

    history = client.get("/agent/conversations", headers=headers)
    assert history.status_code == 200
    assert history.get_json()["conversations"][0]["message_count"] == 4

    detail = client.get(
        f"/agent/conversations/{conversation_id}", headers=headers
    )
    assert detail.status_code == 200
    assert [item["role"] for item in detail.get_json()["messages"]] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]

    with app.app_context():
        assistant = AgentMessage.query.filter_by(role="assistant").first()
        assert assistant.input_tokens == 12
        assert assistant.output_tokens == 7


def test_conversations_are_private_and_can_be_deleted(app, client, monkeypatch):
    monkeypatch.setattr(agent_chat_service, "create_reply", fake_reply)
    owner_headers = auth_headers(app)
    other_headers = auth_headers(app, 1)
    created = client.post(
        "/agent/chat", json={"message": "Private question"}, headers=owner_headers
    ).get_json()
    conversation_id = created["conversation"]["id"]

    assert (
        client.get(
            f"/agent/conversations/{conversation_id}", headers=other_headers
        ).status_code
        == 404
    )
    assert (
        client.delete(
            f"/agent/conversations/{conversation_id}", headers=other_headers
        ).status_code
        == 404
    )
    assert (
        client.delete(
            f"/agent/conversations/{conversation_id}", headers=owner_headers
        ).status_code
        == 204
    )
    assert client.get("/agent/conversations", headers=owner_headers).get_json() == {
        "conversations": []
    }

    with app.app_context():
        assert AgentConversation.query.filter_by(public_id=conversation_id).one().is_deleted


def test_unconfigured_agent_fails_before_persisting(app, client):
    app.config["AGENT_ENABLED"] = False
    response = client.post(
        "/agent/chat",
        json={"message": "hello"},
        headers=auth_headers(app),
    )
    assert response.status_code == 503
    assert response.get_json()["code"] == "agent_unavailable"
    with app.app_context():
        assert AgentConversation.query.count() == 0


def test_provider_failure_preserves_user_message_for_history(
    app, client, monkeypatch
):
    from app.services.agent_chat_service import AgentResponseError

    def fail(_messages):
        raise AgentResponseError("provider failed")

    monkeypatch.setattr(agent_chat_service, "create_reply", fail)
    response = client.post(
        "/agent/chat",
        json={"message": "Please retry later"},
        headers=auth_headers(app),
    )
    assert response.status_code == 502
    payload = response.get_json()
    assert payload["code"] == "agent_request_failed"
    assert payload["conversation"]["message_count"] == 1
    assert payload["user_message"]["content"] == "Please retry later"


@pytest.mark.parametrize("message", ["", "   ", None])
def test_message_validation_rejects_empty_input(app, client, message):
    response = client.post(
        "/agent/chat",
        json={"message": message},
        headers=auth_headers(app),
    )
    assert response.status_code == 400
    assert response.get_json()["code"] == "message_required"


def test_status_never_exposes_provider_key(app, client):
    response = client.get("/agent/status", headers=auth_headers(app))
    assert response.status_code == 200
    assert response.get_json() == {
        "enabled": True,
        "configured": True,
        "model": "test-model",
    }
    assert "test-key" not in response.get_data(as_text=True)
