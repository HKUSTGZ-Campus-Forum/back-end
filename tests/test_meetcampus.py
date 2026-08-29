import pytest
from datetime import datetime, timezone
from flask_jwt_extended import create_access_token
from sqlalchemy.dialects import postgresql

from app import create_app
from app.extensions import db
from app.models.user import User
from app.models.user_role import UserRole
from app.models.meetcampus import (
    MeetCampusActivityDefinition,
    MeetCampusActivityParticipant,
    MeetCampusActivitySession,
    MeetCampusDecision,
    MeetCampusEvent,
    MeetCampusJourney,
    MeetCampusObservation,
    MeetCampusResident,
    MeetCampusResidentState,
    MeetCampusScene,
    MeetCampusSceneConnection,
    MeetCampusStory,
    MeetCampusWorld,
)
from app.services.meetcampus_service import advance_world
from app.services.meetcampus_runtime import _participant_resident_filter


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
        db.session.add_all([
            MeetCampusSceneConnection(
                id="mc-test-campus-gym", world_id="mc-world-campus-v1",
                from_scene_id="mc-scene-campus", to_scene_id="mc-scene-gym",
                travel_minutes=3, path=[],
            ),
            MeetCampusSceneConnection(
                id="mc-test-gym-campus", world_id="mc-world-campus-v1",
                from_scene_id="mc-scene-gym", to_scene_id="mc-scene-campus",
                travel_minutes=3, path=[],
            ),
        ])
        db.session.add(MeetCampusActivityDefinition(
            id="mc-activity-test-badminton", world_id="mc-world-campus-v1",
            scene_id="mc-scene-gym", slug="badminton", name_zh="打一局羽毛球",
            name_en="Play badminton", description_zh="打一局羽毛球", description_en="Play badminton",
            min_participants=2, max_participants=2, duration_min_minutes=12,
            duration_max_minutes=12, capacity=4, requirements={}, effects={"energy": -10},
            outcome_rules={"kind": "competitive", "skill": "badminton"}, tags=["badminton", "social"],
        ))
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
                scene_id="mc-scene-campus" if resident.id == "mc-resident-mount" else "mc-scene-gym",
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


def test_chat_request_receives_schema_contract_and_normalizes_action(app, monkeypatch):
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
    assert len(calls) == 1
    assert calls[0][0].endswith("/chat/completions")
    system_prompt = calls[0][1]["json"]["messages"][0]["content"]
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
    ("patch", "/meetcampus/appearance"),
    ("post", "/meetcampus/commands"),
    ("post", "/meetcampus/stories/not-a-story/view"),
    ("post", "/meetcampus/stories/not-a-story/bridge"),
    ("post", "/meetcampus/memories/corrections"),
    ("get", "/meetcampus/worker/status"),
    ("get", "/meetcampus/debug/residents/mc-resident-lin/traces"),
])
def test_every_route_denies_non_invited_accounts(app, client, method, path):
    email_slug = path.strip("/").replace("/", "-")
    other_id = create_user(app, email=f"other-{method}-{email_slug}@connect.hkust-gz.edu.cn")
    response = getattr(client, method)(
        path,
        headers=auth_headers(app, other_id),
        json={} if method in {"post", "patch"} else None,
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


def test_homecoming_participant_filter_uses_postgresql_jsonb_containment():
    predicate = _participant_resident_filter("mc-resident-mount", dialect_name="postgresql")
    sql = str(predicate.compile(dialect=postgresql.dialect()))

    assert "@>" in sql
    assert "LIKE" not in sql


def test_bootstrap_compiles_homecoming_for_participant_event(app, client):
    invited_id = create_user(app, email="wtao565@connect.hkust-gz.edu.cn")
    headers = auth_headers(app, invited_id)
    onboarding = client.post("/meetcampus/onboarding", headers=headers, json={
        "locale": "zh",
        "autonomyLevel": "balanced",
        "anchors": {"residentName": "小满"},
    })
    assert onboarding.status_code == 200
    with app.app_context():
        db.session.add(MeetCampusEvent(
            id="mc-event-participant-homecoming",
            world_id="mc-world-campus-v1",
            scene_id="mc-scene-gym",
            actor_resident_id="mc-resident-lin",
            kind="activity_completed",
            summary_zh="阿林和小满打完了一局羽毛球。",
            summary_en="Lin and Mori finished a badminton match.",
            participant_resident_ids=["mc-resident-mount"],
            payload={"result": {"kind": "competitive"}},
            importance=5,
            idempotency_key="test-participant-homecoming",
        ))
        db.session.commit()

    response = client.get(
        "/meetcampus/bootstrap",
        headers=headers,
    )

    assert response.status_code == 200
    stories = response.get_json()["stories"]
    assert stories
    with app.app_context():
        story = MeetCampusStory.query.filter_by(owner_user_id=invited_id).one()
        assert story.event_ids == ["mc-event-participant-homecoming"]


def test_onboarding_command_and_world_tick_are_persistent(app, client):
    invited_id = create_user(app, email="wtao565@connect.hkust-gz.edu.cn")
    headers = auth_headers(app, invited_id)
    response = client.post("/meetcampus/onboarding", headers=headers, json={
        "locale": "zh",
        "autonomyLevel": "balanced",
        "appearance": {
            "skinTone": "tan",
            "hairStyle": "waves",
            "hairColor": "auburn",
            "outfit": "mint_cardigan",
            "accessory": "round_glasses",
        },
        "anchors": {
            "residentName": "小满",
            "socialPace": "slow_warmup",
            "preferredPlaces": ["gym"],
            "ownerNote": "替我看看今天校园里有什么新鲜事。",
        },
    })
    assert response.status_code == 200
    assert len(response.get_json()["snapshot"]["residents"]) == 2
    mine = next(item for item in response.get_json()["snapshot"]["residents"] if item["isMine"])
    assert mine["appearance"] == {
        "skinTone": "tan", "hairStyle": "waves", "hairColor": "auburn",
        "outfit": "mint_cardigan", "accessory": "round_glasses",
    }

    restyled = client.patch("/meetcampus/appearance", headers=headers, json={
        "hairStyle": "bun", "accessory": "hairclip",
    })
    assert restyled.status_code == 200
    mine = next(item for item in restyled.get_json()["snapshot"]["residents"] if item["isMine"])
    assert mine["appearance"]["hairStyle"] == "bun"
    assert mine["appearance"]["accessory"] == "hairclip"
    assert mine["appearance"]["skinTone"] == "tan"

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
        journey = MeetCampusJourney.query.filter_by(resident_id="mc-resident-mount", status="traveling").one()
        assert int((journey.arrive_at - journey.depart_at).total_seconds()) == 180
        assert journey.from_scene_id == "mc-scene-campus"
        assert journey.to_scene_id == "mc-scene-gym"
        repeated = advance_world(max_residents=2)
        assert repeated["advancedResidents"] == 0
        assert repeated["stateVersion"] == first_version
        arrived = advance_world(now=journey.arrive_at, max_residents=2)
        assert arrived["completedJourneys"] == 1
        final_version = arrived["stateVersion"]

    snapshot = client.get("/meetcampus/snapshot", headers=headers)
    assert snapshot.status_code == 200
    payload = snapshot.get_json()
    assert payload["snapshot"]["world"]["stateVersion"] == final_version
    assert payload["stories"]


def test_perspective_switch_uses_same_runtime_and_hides_provenance_from_cognition(app, client):
    invited_id = create_user(app, email="wtao565@connect.hkust-gz.edu.cn")
    headers = auth_headers(app, invited_id)
    client.post("/meetcampus/onboarding", headers=headers, json={
        "locale": "zh", "autonomyLevel": "balanced", "anchors": {"residentName": "小满"},
    })

    switched = client.get(
        "/meetcampus/bootstrap?residentId=mc-resident-lin",
        headers=headers,
    )
    assert switched.status_code == 200
    payload = switched.get_json()
    assert payload["ownerResidentId"] == "mc-resident-mount"
    assert payload["myResidentId"] == "mc-resident-lin"
    assert payload["perspective"]["isOwnerResident"] is False
    assert payload["feature"]["runtimeParity"] is True

    command = client.post("/meetcampus/commands", headers=headers, json={
        "residentId": "mc-resident-lin", "kind": "visit", "text": "去主校园走走",
        "targetSceneId": "mc-scene-campus",
    })
    assert command.status_code == 201
    with app.app_context():
        advance_world(max_residents=2)
        observation = MeetCampusObservation.query.filter_by(resident_id="mc-resident-lin").order_by(
            MeetCampusObservation.observed_at.desc()
        ).first()
        decision = MeetCampusDecision.query.filter_by(resident_id="mc-resident-lin").order_by(
            MeetCampusDecision.created_at.desc()
        ).first()
        assert observation is not None and decision is not None
        assert "synthetic" not in str(observation.payload).casefold()
        assert "is_synthetic" not in str(observation.payload).casefold()
        assert decision.selected_intent["kind"] == "travel"

    traces = client.get(
        "/meetcampus/debug/residents/mc-resident-lin/traces",
        headers=headers,
    )
    assert traces.status_code == 200
    assert traces.get_json()["traces"][0]["selectedIntent"]["kind"] == "travel"


def test_shared_activity_requires_independent_acceptance_and_world_resolves_score(app):
    with app.app_context():
        now = datetime.now(timezone.utc)
        mount = db.session.get(MeetCampusResident, "mc-resident-mount")
        mount.is_active = True
        for resident_id in ("mc-resident-mount", "mc-resident-lin"):
            state = db.session.get(MeetCampusResidentState, resident_id)
            state.scene_id = "mc-scene-gym"
            state.next_decision_at = now
            state.needs = {"energy": 75, "social": 65}
        db.session.commit()

        formed = advance_world(now=now, max_residents=2)
        assert formed["advancedResidents"] == 2
        session = MeetCampusActivitySession.query.one()
        participants = MeetCampusActivityParticipant.query.filter_by(session_id=session.id).all()
        assert session.status == "active"
        assert {participant.status for participant in participants} == {"accepted"}
        assert {participant.resident_id for participant in participants} == {
            "mc-resident-mount", "mc-resident-lin",
        }

        finished = advance_world(now=session.ends_at, max_residents=2)
        assert finished["completedSessions"] == 1
        event = MeetCampusEvent.query.filter_by(kind="shared_activity").one()
        assert event.payload["factSource"] == "world_kernel"
        assert event.payload["result"]["kind"] == "competitive"
        assert sorted(event.payload["result"]["score"].values())[1] == 21
        assert "21" in event.summary_zh


def test_appearance_rejects_invalid_options_and_requires_onboarding(app, client):
    invited_id = create_user(app, email="wtao565@connect.hkust-gz.edu.cn")
    headers = auth_headers(app, invited_id)

    before_onboarding = client.patch("/meetcampus/appearance", headers=headers, json={"hairStyle": "bun"})
    assert before_onboarding.status_code == 409
    assert before_onboarding.get_json()["code"] == "onboarding_required"

    invalid = client.post("/meetcampus/onboarding", headers=headers, json={
        "locale": "zh", "autonomyLevel": "balanced", "anchors": {},
        "appearance": {"hairStyle": "impossible"},
    })
    assert invalid.status_code == 400
    assert invalid.get_json()["code"] == "invalid_appearance"
