import pytest
from datetime import datetime, timezone
from flask_jwt_extended import create_access_token

from app import create_app
from app.extensions import db
from app.models.user import User
from app.models.user_role import UserRole
from app.models.meetcampus import (
    MeetCampusResident,
    MeetCampusResidentState,
    MeetCampusScene,
    MeetCampusWorld,
)
from app.services.meetcampus_service import advance_world


class TestConfig:
    TESTING = True
    SECRET_KEY = "meetcampus-test-secret"
    JWT_SECRET_KEY = "meetcampus-test-jwt-secret-with-at-least-32-bytes"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_ENGINE_OPTIONS = {}
    CACHE_TYPE = "SimpleCache"
    AUTO_INIT_ON_STARTUP = False
    ENABLE_BACKGROUND_TASKS = False
    CAMPUS_SSO_ENABLED = False
    MEETCAMPUS_BETA_EMAILS = ("wtao565@connect.hkust-gz.edu.cn",)
    MEETCAMPUS_WORLD_ID = "mc-world-campus-v1"
    MEETCAMPUS_WORLD_ENABLED = True
    MEETCAMPUS_WORLD_TICK_SECONDS = 60
    MEETCAMPUS_DECISION_MIN_MINUTES = 30
    MEETCAMPUS_DECISION_MAX_MINUTES = 90
    MEETCAMPUS_MAX_DUE_RESIDENTS_PER_TICK = 8
    MEETCAMPUS_AI_API_KEY = ""
    MEETCAMPUS_AI_API_BASE = "https://aigw.example/v1"
    MEETCAMPUS_AI_MODEL = "DeepSeek-V4-Flash"
    MEETCAMPUS_AI_TIMEOUT_SECONDS = 30
    MEETCAMPUS_AI_DAILY_CALL_BUDGET = 0


@pytest.fixture
def app():
    application = create_app(TestConfig)
    with application.app_context():
        db.create_all()
        db.session.add(UserRole(name=UserRole.USER))
        now = datetime.now(timezone.utc)
        db.session.add(MeetCampusWorld(
            id="mc-world-campus-v1",
            slug="campus-v1",
            name_zh="MeetCampus 校园",
            name_en="MeetCampus Campus",
            status="active",
            seed_version="test",
            state_version=1,
            last_advanced_at=now,
        ))
        db.session.add_all([
            MeetCampusScene(
                id="mc-scene-campus",
                world_id="mc-world-campus-v1",
                slug="campus",
                kind="campus",
                name_zh="主校园",
                name_en="Main Campus",
                map_x=50,
                map_y=50,
                affordances=["walk"],
                visual={},
            ),
            MeetCampusScene(
                id="mc-scene-gym",
                world_id="mc-world-campus-v1",
                parent_scene_id="mc-scene-campus",
                slug="gym",
                kind="sport",
                name_zh="体育馆",
                name_en="Gym",
                map_x=60,
                map_y=20,
                affordances=["badminton"],
                visual={},
            ),
        ])
        residents = [
            MeetCampusResident(
                id="mc-resident-mount",
                world_id="mc-world-campus-v1",
                slug="mount",
                is_synthetic=False,
                name_zh="小满",
                name_en="Mori",
                pronouns={}, persona={"interests": ["羽毛球"]},
                appearance={"palette": "navy"}, schedule=[], voice={},
                is_active=False,
            ),
            MeetCampusResident(
                id="mc-resident-lin",
                world_id="mc-world-campus-v1",
                slug="lin",
                is_synthetic=True,
                name_zh="林夕",
                name_en="Lin",
                pronouns={}, persona={"interests": ["羽毛球"]},
                appearance={"palette": "green"}, schedule=[], voice={},
                is_active=True,
            ),
        ]
        db.session.add_all(residents)
        db.session.add_all([
            MeetCampusResidentState(
                resident_id=resident.id,
                scene_id="mc-scene-gym",
                position_x=50,
                position_y=50,
                activity="settling_in",
                activity_started_at=now,
                needs={}, active_goal={}, next_decision_at=now,
            ) for resident in residents
        ])
        db.session.commit()
    return application


@pytest.fixture
def client(app):
    return app.test_client()


def create_user(app, *, email, verified=True, deleted=False):
    with app.app_context():
        role = UserRole.query.filter_by(name=UserRole.USER).one()
        user = User(
            username=f"meet-{User.query.count() + 1}",
            email=email,
            email_verified=verified,
            role_id=role.id,
            is_deleted=deleted,
        )
        user.set_password("unused-sso-placeholder")
        db.session.add(user)
        db.session.commit()
        return user.id


def auth_headers(app, user_id):
    with app.app_context():
        token = create_access_token(identity=str(user_id))
    return {"Authorization": f"Bearer {token}"}


def test_bootstrap_requires_authentication(client):
    response = client.get("/meetcampus/bootstrap")
    assert response.status_code == 401


def test_chat_fallback_receives_schema_contract_and_normalizes_action(app, monkeypatch):
    from app.services import meetcampus_ai

    calls = []

    class FakeResponse:
        def __init__(self, status_code, payload):
            self.status_code = status_code
            self._payload = payload

        def json(self):
            return self._payload

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        if url.endswith("/responses"):
            return FakeResponse(404, {})
        return FakeResponse(200, {
            "choices": [{"message": {"content": (
                '{"action":"socialize","scene_slug":"gym","affordance":"chat",'
                '"target_resident_id":null,"intention_zh":"聊聊天",'
                '"intention_en":"Have a chat"}'
            )}}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 8},
        })

    monkeypatch.setattr(meetcampus_ai.requests, "post", fake_post)
    with app.app_context():
        app.config["MEETCAMPUS_AI_API_KEY"] = "sk-test-provider-key-123456"
        app.config["MEETCAMPUS_AI_DAILY_CALL_BUDGET"] = 10
        decision = meetcampus_ai.propose_action(
            resident={"id": "mc-resident-lin", "name": "Lin"},
            observation={"scene_slug": "gym", "available_scene_slugs": ["gym"]},
        )

    assert decision is not None
    assert decision.action == "talk"
    assert calls[0][0].endswith("/responses")
    assert calls[1][0].endswith("/chat/completions")
    system_prompt = calls[1][1]["json"]["messages"][0]["content"]
    assert '"enum":["activity","move","observe","rest","talk"]' in system_prompt


def test_bootstrap_denies_non_invited_and_unverified_accounts(app, client):
    other_id = create_user(app, email="other@connect.hkust-gz.edu.cn")
    unverified_id = create_user(
        app,
        email="wtao565@connect.hkust-gz.edu.cn",
        verified=False,
    )

    for user_id in (other_id, unverified_id):
        response = client.get(
            "/meetcampus/bootstrap",
            headers=auth_headers(app, user_id),
        )
        assert response.status_code == 403
        assert response.get_json()["code"] == "meetcampus_beta_required"
        assert response.headers["Cache-Control"] == "private, no-store"


@pytest.mark.parametrize(("method", "path"), [
    ("get", "/meetcampus/bootstrap"),
    ("get", "/meetcampus/snapshot"),
    ("post", "/meetcampus/onboarding"),
    ("post", "/meetcampus/commands"),
    ("post", "/meetcampus/stories/not-a-story/view"),
    ("post", "/meetcampus/stories/not-a-story/bridge"),
    ("post", "/meetcampus/memories/corrections"),
    ("get", "/meetcampus/worker/status"),
])
def test_every_route_denies_non_invited_accounts(app, client, method, path):
    email_slug = path.strip("/").replace("/", "-")
    other_id = create_user(app, email=f"other-{method}-{email_slug}@connect.hkust-gz.edu.cn")
    response = getattr(client, method)(
        path,
        headers=auth_headers(app, other_id),
        json={} if method == "post" else None,
    )
    assert response.status_code == 403
    assert response.get_json()["code"] == "meetcampus_beta_required"
    assert response.headers["Cache-Control"] == "private, no-store"


def test_bootstrap_allows_only_normalized_invited_email(app, client):
    invited_id = create_user(
        app,
        email="  WTAO565@CONNECT.HKUST-GZ.EDU.CN  ",
    )

    response = client.get(
        "/meetcampus/bootstrap",
        headers=auth_headers(app, invited_id),
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["feature"]["mode"] == "persistent_world"
    assert payload["feature"]["sessionStorage"] == "server"
    assert payload["feature"]["liveAgents"] is True
    assert payload["feature"]["syntheticResidentCount"] == 19
    assert payload["onboarding"]["status"] == "not_started"
    assert payload["myResidentId"] == "mc-resident-mount"
    assert len(payload["snapshot"]["residents"]) == 1
    assert response.headers["Cache-Control"] == "private, no-store"


def test_onboarding_command_and_world_tick_are_persistent(app, client):
    invited_id = create_user(app, email="wtao565@connect.hkust-gz.edu.cn")
    headers = auth_headers(app, invited_id)
    response = client.post("/meetcampus/onboarding", headers=headers, json={
        "locale": "zh",
        "autonomyLevel": "balanced",
        "anchors": {
            "residentName": "小满",
            "socialPace": "slow_warmup",
            "preferredPlaces": ["gym"],
            "ownerNote": "替我看看今天校园里有什么新鲜事。",
        },
    })
    assert response.status_code == 200
    assert len(response.get_json()["snapshot"]["residents"]) == 2

    command = client.post("/meetcampus/commands", headers=headers, json={
        "kind": "visit",
        "text": "去体育馆看看有没有人打羽毛球",
        "targetSceneId": "mc-scene-gym",
    })
    assert command.status_code == 201

    with app.app_context():
        result = advance_world(max_residents=2)
        assert result["advancedResidents"] == 2
        first_version = result["stateVersion"]
        repeated = advance_world(max_residents=2)
        assert repeated["advancedResidents"] == 0
        assert repeated["stateVersion"] == first_version

    snapshot = client.get("/meetcampus/snapshot", headers=headers)
    assert snapshot.status_code == 200
    payload = snapshot.get_json()
    assert payload["snapshot"]["world"]["stateVersion"] == first_version
    assert payload["stories"]
