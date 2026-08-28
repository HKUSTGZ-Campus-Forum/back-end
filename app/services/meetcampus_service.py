"""Persistent MeetCampus private-beta domain service."""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from typing import Any

from flask import current_app

from app.extensions import db
from app.models.meetcampus import (
    MeetCampusBridge,
    MeetCampusCommand,
    MeetCampusEvent,
    MeetCampusMemory,
    MeetCampusOwnerProfile,
    MeetCampusRelationship,
    MeetCampusResident,
    MeetCampusResidentState,
    MeetCampusScene,
    MeetCampusStory,
    MeetCampusWorld,
)
from app.models.user import User
from app.services.meetcampus_ai import (
    AgentDecision,
    narrate_event,
    propose_action,
    provider_configured,
)


MOUNT_RESIDENT_SLUG = "mount"
ALLOWED_AUTONOMY = frozenset({"guided", "balanced", "brave"})
ALLOWED_COMMAND_KINDS = frozenset({"goal", "visit", "activity"})
APPEARANCE_OPTIONS = {
    "skinTone": ("porcelain", "warm", "tan", "deep"),
    "hairStyle": ("crop", "bob", "waves", "bun", "curly", "cap"),
    "hairColor": ("ink", "chestnut", "auburn", "plum", "ocean"),
    "outfit": ("campus_blue", "mint_cardigan", "sunset_hoodie", "lavender_knit", "sport_green", "lab_coat"),
    "accessory": ("none", "round_glasses", "headphones", "beret", "hairclip"),
}
DEFAULT_APPEARANCE = {
    "skinTone": "warm",
    "hairStyle": "crop",
    "hairColor": "ink",
    "outfit": "campus_blue",
    "accessory": "none",
}
LEGACY_PALETTE_OUTFITS = {
    "navy": "campus_blue", "blue": "campus_blue", "green": "sport_green",
    "forest": "sport_green", "mint": "mint_cardigan", "orange": "sunset_hoodie",
    "amber": "sunset_hoodie", "purple": "lavender_knit",
}
DEFAULT_PRIVACY_RULES = {
    "share_virtual_interests": True,
    "share_real_name": False,
    "share_contact": False,
    "share_schedule": False,
    "real_world_commitments_require_consent": True,
}


class MeetCampusDomainError(RuntimeError):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def normalize_email(value: str | None) -> str:
    return (value or "").strip().casefold()


def configured_beta_emails() -> frozenset[str]:
    configured = current_app.config.get("MEETCAMPUS_BETA_EMAILS", ())
    values: Iterable[str] = configured.split(",") if isinstance(configured, str) else configured or ()
    return frozenset(normalize_email(value) for value in values if normalize_email(value))


def can_access_meetcampus(user: User | None) -> bool:
    if user is None or user.is_deleted or not user.email_verified:
        return False
    email = normalize_email(user.email)
    return bool(email and email in configured_beta_emails())


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _localized(zh: str, en: str) -> dict[str, str]:
    return {"zh": zh, "en": en}


def _legacy_option(options: tuple[str, ...], value: Any, fallback: str) -> str:
    try:
        return options[abs(int(value)) % len(options)]
    except (TypeError, ValueError):
        return fallback


def normalize_appearance(value: Any) -> dict[str, str]:
    """Expose one stable appearance contract while retaining legacy seed compatibility."""
    source = value if isinstance(value, dict) else {}
    result = dict(DEFAULT_APPEARANCE)
    for key, options in APPEARANCE_OPTIONS.items():
        if source.get(key) in options:
            result[key] = source[key]
    if source.get("hairStyle") not in APPEARANCE_OPTIONS["hairStyle"]:
        result["hairStyle"] = _legacy_option(APPEARANCE_OPTIONS["hairStyle"], source.get("hair"), result["hairStyle"])
    if source.get("hairColor") not in APPEARANCE_OPTIONS["hairColor"]:
        result["hairColor"] = _legacy_option(APPEARANCE_OPTIONS["hairColor"], source.get("hair"), result["hairColor"])
    if source.get("outfit") not in APPEARANCE_OPTIONS["outfit"]:
        result["outfit"] = LEGACY_PALETTE_OUTFITS.get(str(source.get("palette"))) or _legacy_option(
            APPEARANCE_OPTIONS["outfit"], source.get("outfit"), result["outfit"]
        )
    if source.get("accessory") not in APPEARANCE_OPTIONS["accessory"]:
        result["accessory"] = _legacy_option(APPEARANCE_OPTIONS["accessory"], source.get("accessory"), result["accessory"])
    return result


def validate_appearance(value: Any, current: Any = None) -> dict[str, str]:
    if not isinstance(value, dict):
        raise MeetCampusDomainError("invalid_appearance", "Resident appearance must be an object.")
    unknown = set(value) - set(APPEARANCE_OPTIONS)
    if unknown:
        raise MeetCampusDomainError("invalid_appearance", "Resident appearance contains unsupported fields.")
    result = normalize_appearance(current)
    for key, option in value.items():
        if option not in APPEARANCE_OPTIONS[key]:
            raise MeetCampusDomainError("invalid_appearance", f"Unsupported appearance option for {key}.")
        result[key] = option
    return result


def _world() -> MeetCampusWorld:
    world = db.session.get(
        MeetCampusWorld,
        current_app.config.get("MEETCAMPUS_WORLD_ID", "mc-world-campus-v1"),
    )
    if world is None:
        raise MeetCampusDomainError(
            "meetcampus_not_initialized",
            "MeetCampus product data is not initialized.",
            503,
        )
    return world


def ensure_owner_profile(user: User) -> tuple[MeetCampusResident, MeetCampusOwnerProfile]:
    profile = MeetCampusOwnerProfile.query.filter_by(owner_user_id=user.id).one_or_none()
    if profile is not None:
        resident = db.session.get(MeetCampusResident, profile.resident_id)
        if resident is None:
            raise MeetCampusDomainError("resident_missing", "Your resident could not be found.", 503)
        return resident, profile

    resident = MeetCampusResident.query.filter_by(slug=MOUNT_RESIDENT_SLUG).one_or_none()
    if resident is None:
        raise MeetCampusDomainError("resident_missing", "The private-beta resident is unavailable.", 503)
    if resident.owner_user_id not in (None, user.id):
        raise MeetCampusDomainError("resident_already_claimed", "The private-beta resident is already claimed.", 409)

    resident.owner_user_id = user.id
    resident.is_active = False
    profile = MeetCampusOwnerProfile(
        owner_user_id=user.id,
        resident_id=resident.id,
        onboarding_status="not_started",
        locale="zh",
        autonomy_level="balanced",
        anchors={},
        privacy_rules=dict(DEFAULT_PRIVACY_RULES),
    )
    db.session.add(profile)
    db.session.commit()
    return resident, profile


def _serialize_scene(scene: MeetCampusScene) -> dict[str, Any]:
    return {
        "id": scene.id,
        "slug": scene.slug,
        "parentSceneId": scene.parent_scene_id,
        "kind": scene.kind,
        "name": _localized(scene.name_zh, scene.name_en),
        "map": {"x": scene.map_x, "y": scene.map_y},
        "affordances": list(scene.affordances or []),
        "visual": dict(scene.visual or {}),
    }


def _serialize_resident(
    resident: MeetCampusResident,
    state: MeetCampusResidentState,
    *,
    is_mine: bool = False,
) -> dict[str, Any]:
    persona = dict(resident.persona or {})
    return {
        "id": resident.id,
        "slug": resident.slug,
        "name": _localized(resident.name_zh, resident.name_en),
        "isMine": is_mine,
        "isSynthetic": resident.is_synthetic,
        "appearance": normalize_appearance(resident.appearance),
        "persona": persona if is_mine else {
            "interests": list(persona.get("interests", [])),
            "temperament": persona.get("temperament"),
        },
        "state": {
            "sceneId": state.scene_id,
            "position": {"x": state.position_x, "y": state.position_y},
            "activity": state.activity,
            "activityStartedAt": _iso(state.activity_started_at),
            "nextDecisionAt": _iso(state.next_decision_at) if is_mine else None,
        },
    }


def _snapshot(world: MeetCampusWorld, my_resident: MeetCampusResident) -> dict[str, Any]:
    scenes = MeetCampusScene.query.filter_by(
        world_id=world.id,
        is_active=True,
    ).order_by(MeetCampusScene.slug).all()
    residents = MeetCampusResident.query.filter_by(
        world_id=world.id,
        is_active=True,
    ).order_by(MeetCampusResident.slug).all()
    resident_ids = [resident.id for resident in residents]
    states = {
        state.resident_id: state
        for state in MeetCampusResidentState.query.filter(
            MeetCampusResidentState.resident_id.in_(resident_ids)
        ).all()
    } if resident_ids else {}
    return {
        "world": {
            "id": world.id,
            "name": _localized(world.name_zh, world.name_en),
            "status": world.status,
            "stateVersion": world.state_version,
            "lastAdvancedAt": _iso(world.last_advanced_at),
            "serverTime": _iso(utc_now()),
        },
        "scenes": [_serialize_scene(scene) for scene in scenes],
        "residents": [
            _serialize_resident(resident, states[resident.id], is_mine=resident.id == my_resident.id)
            for resident in residents
            if resident.id in states
        ],
    }


def _serialize_story(story: MeetCampusStory) -> dict[str, Any]:
    events = MeetCampusEvent.query.filter(
        MeetCampusEvent.id.in_(story.event_ids or [])
    ).order_by(MeetCampusEvent.occurred_at).all()
    return {
        "id": story.id,
        "title": _localized(story.title_zh, story.title_en),
        "narration": _localized(story.narration_zh, story.narration_en),
        "events": [{
            "id": event.id,
            "kind": event.kind,
            "summary": _localized(event.summary_zh, event.summary_en),
            "sceneId": event.scene_id,
            "participantResidentIds": list(event.participant_resident_ids or []),
            "importance": event.importance,
            "occurredAt": _iso(event.occurred_at),
        } for event in events],
        "bridgeCandidate": story.bridge_candidate,
        "isViewed": story.is_viewed,
        "createdAt": _iso(story.created_at),
    }


def build_bootstrap_payload(user: User) -> dict[str, Any]:
    world = _world()
    resident, profile = ensure_owner_profile(user)
    stories = MeetCampusStory.query.filter_by(owner_user_id=user.id).order_by(
        MeetCampusStory.created_at.desc()
    ).limit(5).all()
    return {
        "feature": {
            "id": "meetcampus",
            "stage": "private_beta",
            "mode": "persistent_world",
            "sessionStorage": "server",
            "liveAgents": True,
            "realPeople": False,
            "autonomousAgentDecisions": True,
            "syntheticResidentCount": 19,
            "providerConfigured": provider_configured(),
        },
        "onboarding": {
            "status": profile.onboarding_status,
            "completedAt": _iso(profile.completed_at),
            "autonomyLevel": profile.autonomy_level,
            "anchors": dict(profile.anchors or {}),
            "privacyRules": dict(profile.privacy_rules or {}),
        },
        "myResidentId": resident.id,
        "snapshot": _snapshot(world, resident),
        "stories": [_serialize_story(story) for story in stories],
        "relationships": list_relationships(resident),
    }


def complete_onboarding(user: User, payload: dict[str, Any]) -> dict[str, Any]:
    resident, profile = ensure_owner_profile(user)
    autonomy = str(payload.get("autonomyLevel") or "balanced")
    if autonomy not in ALLOWED_AUTONOMY:
        raise MeetCampusDomainError("invalid_autonomy", "Invalid resident autonomy level.")
    locale = str(payload.get("locale") or "zh")
    if locale not in {"zh", "en"}:
        raise MeetCampusDomainError("invalid_locale", "Invalid locale.")
    raw_anchors = payload.get("anchors")
    if not isinstance(raw_anchors, dict):
        raise MeetCampusDomainError("invalid_anchors", "Resident anchors must be an object.")
    allowed = {"socialPace", "preferredPlaces", "ownerNote", "residentName", "bravery"}
    anchors = {key: raw_anchors[key] for key in allowed if key in raw_anchors}
    owner_note = str(anchors.get("ownerNote") or "").strip()
    if len(owner_note) > 280:
        raise MeetCampusDomainError("owner_note_too_long", "Owner note must be 280 characters or fewer.")
    anchors["ownerNote"] = owner_note

    privacy = dict(DEFAULT_PRIVACY_RULES)
    requested_privacy = payload.get("privacyRules") or {}
    if isinstance(requested_privacy, dict):
        privacy["share_virtual_interests"] = bool(
            requested_privacy.get("share_virtual_interests", True)
        )
    resident_name = str(anchors.get("residentName") or "").strip()
    if resident_name:
        if not 1 <= len(resident_name) <= 20:
            raise MeetCampusDomainError(
                "invalid_resident_name",
                "Resident name must be between 1 and 20 characters.",
            )
        resident.name_zh = resident_name
        resident.name_en = resident_name
    resident.persona = {
        **dict(resident.persona or {}),
        "ownerAnchors": anchors,
        "autonomyLevel": autonomy,
    }
    if "appearance" in payload:
        resident.appearance = validate_appearance(payload["appearance"], resident.appearance)
    else:
        resident.appearance = normalize_appearance(resident.appearance)
    resident.is_active = True
    profile.locale = locale
    profile.autonomy_level = autonomy
    profile.anchors = anchors
    profile.privacy_rules = privacy
    profile.onboarding_status = "completed"
    profile.completed_at = utc_now()
    state = db.session.get(MeetCampusResidentState, resident.id)
    if state:
        state.activity = "setting_out"
        state.activity_started_at = utc_now()
        state.next_decision_at = utc_now()
    db.session.commit()
    return build_bootstrap_payload(user)


def update_appearance(user: User, payload: dict[str, Any]) -> dict[str, Any]:
    resident, profile = ensure_owner_profile(user)
    if profile.onboarding_status != "completed":
        raise MeetCampusDomainError("onboarding_required", "Finish meeting your resident first.", 409)
    resident.appearance = validate_appearance(payload, resident.appearance)
    db.session.commit()
    return build_bootstrap_payload(user)


def create_command(user: User, payload: dict[str, Any]) -> dict[str, Any]:
    resident, profile = ensure_owner_profile(user)
    if profile.onboarding_status != "completed":
        raise MeetCampusDomainError("onboarding_required", "Finish meeting your resident first.", 409)
    kind = str(payload.get("kind") or "goal")
    if kind not in ALLOWED_COMMAND_KINDS:
        raise MeetCampusDomainError("invalid_command_kind", "Invalid command kind.")
    text = str(payload.get("text") or "").strip()
    if not 1 <= len(text) <= 280:
        raise MeetCampusDomainError(
            "invalid_command_text",
            "Command text must be between 1 and 280 characters.",
        )
    command_payload: dict[str, Any] = {}
    target_scene_id = payload.get("targetSceneId")
    if target_scene_id:
        scene = db.session.get(MeetCampusScene, str(target_scene_id))
        if scene is None or scene.world_id != resident.world_id or not scene.is_active:
            raise MeetCampusDomainError("invalid_target_scene", "That scene is unavailable.")
        command_payload["targetSceneId"] = scene.id
    command = MeetCampusCommand(
        id=str(uuid.uuid4()),
        owner_user_id=user.id,
        resident_id=resident.id,
        kind=kind,
        text=text,
        payload=command_payload,
        status="pending",
        outcome={},
    )
    db.session.add(command)
    state = db.session.get(MeetCampusResidentState, resident.id)
    if state:
        state.next_decision_at = utc_now()
    db.session.commit()
    return {"id": command.id, "status": command.status, "createdAt": _iso(command.created_at)}


def mark_story_viewed(user: User, story_id: str) -> dict[str, Any]:
    story = MeetCampusStory.query.filter_by(id=story_id, owner_user_id=user.id).one_or_none()
    if story is None:
        raise MeetCampusDomainError("story_not_found", "Story not found.", 404)
    if not story.is_viewed:
        story.is_viewed = True
        story.viewed_at = utc_now()
        db.session.commit()
    return _serialize_story(story)


def correct_memory(user: User, payload: dict[str, Any]) -> dict[str, Any]:
    resident, profile = ensure_owner_profile(user)
    if profile.onboarding_status != "completed":
        raise MeetCampusDomainError("onboarding_required", "Finish meeting your resident first.", 409)
    correction_zh = str(payload.get("correctionZh") or "").strip()
    correction_en = str(payload.get("correctionEn") or correction_zh).strip()
    if not 1 <= len(correction_zh) <= 280 or len(correction_en) > 420:
        raise MeetCampusDomainError("invalid_correction", "Correction text is invalid.")
    supersedes_id = payload.get("memoryId")
    superseded = None
    if supersedes_id:
        superseded = MeetCampusMemory.query.filter_by(
            id=str(supersedes_id),
            resident_id=resident.id,
        ).one_or_none()
        if superseded is None:
            raise MeetCampusDomainError("memory_not_found", "Memory not found.", 404)
    memory = MeetCampusMemory(
        id=str(uuid.uuid4()),
        resident_id=resident.id,
        kind="owner_correction",
        content_zh=correction_zh,
        content_en=correction_en,
        source_event_ids=[],
        source="owner_explicit",
        salience=10,
        confidence=1.0,
    )
    db.session.add(memory)
    db.session.flush()
    if superseded:
        superseded.superseded_by_id = memory.id
    db.session.commit()
    return {"id": memory.id, "createdAt": _iso(memory.created_at)}


def list_relationships(resident: MeetCampusResident) -> list[dict[str, Any]]:
    rows = MeetCampusRelationship.query.filter(
        (MeetCampusRelationship.resident_a_id == resident.id)
        | (MeetCampusRelationship.resident_b_id == resident.id)
    ).order_by(MeetCampusRelationship.updated_at.desc()).all()
    result = []
    for row in rows:
        other_id = row.resident_b_id if row.resident_a_id == resident.id else row.resident_a_id
        other = db.session.get(MeetCampusResident, other_id)
        if other is None:
            continue
        result.append({
            "id": row.id,
            "resident": {
                "id": other.id,
                "name": _localized(other.name_zh, other.name_en),
                "appearance": normalize_appearance(other.appearance),
                "isSynthetic": other.is_synthetic,
            },
            "familiarity": row.familiarity,
            "trust": row.trust,
            "warmth": row.warmth,
            "sharedInterests": list(row.shared_interests or []),
            "summary": _localized(row.summary_zh, row.summary_en),
            "updatedAt": _iso(row.updated_at),
        })
    return result


def create_bridge(user: User, story_id: str) -> dict[str, Any]:
    resident, _profile = ensure_owner_profile(user)
    story = MeetCampusStory.query.filter_by(
        id=story_id,
        owner_user_id=user.id,
        resident_id=resident.id,
    ).one_or_none()
    if story is None or not story.bridge_candidate:
        raise MeetCampusDomainError(
            "bridge_unavailable",
            "This story is not ready for a real-world bridge.",
            409,
        )
    events = MeetCampusEvent.query.filter(
        MeetCampusEvent.id.in_(story.event_ids or [])
    ).order_by(MeetCampusEvent.importance.desc()).all()
    source_event = next((event for event in events if event.participant_resident_ids), None)
    if source_event is None:
        raise MeetCampusDomainError("bridge_unavailable", "This story has no counterpart.", 409)
    counterpart_id = next(
        (rid for rid in source_event.participant_resident_ids if rid != resident.id),
        None,
    )
    counterpart = db.session.get(MeetCampusResident, counterpart_id) if counterpart_id else None
    if counterpart is None:
        raise MeetCampusDomainError("bridge_unavailable", "The counterpart is unavailable.", 409)
    existing = MeetCampusBridge.query.filter_by(
        source_event_id=source_event.id,
        initiator_owner_user_id=user.id,
    ).one_or_none()
    if existing is not None:
        return _serialize_bridge(existing, counterpart)
    synthetic = counterpart.is_synthetic or counterpart.owner_user_id is None
    bridge = MeetCampusBridge(
        id=str(uuid.uuid4()),
        source_event_id=source_event.id,
        initiator_resident_id=resident.id,
        counterpart_resident_id=counterpart.id,
        initiator_owner_user_id=user.id,
        counterpart_owner_user_id=counterpart.owner_user_id,
        status="synthetic_preview" if synthetic else "counterpart_pending",
        proposal={
            "activity": (source_event.payload or {}).get("affordance", "campus_activity"),
            "disclosure": "synthetic_resident" if synthetic else "real_owner_pending",
        },
        initiator_consented_at=utc_now(),
        expires_at=utc_now() + timedelta(days=7),
    )
    db.session.add(bridge)
    db.session.commit()
    return _serialize_bridge(bridge, counterpart)


def _serialize_bridge(
    bridge: MeetCampusBridge,
    counterpart: MeetCampusResident,
) -> dict[str, Any]:
    return {
        "id": bridge.id,
        "status": bridge.status,
        "counterpart": {
            "residentId": counterpart.id,
            "name": _localized(counterpart.name_zh, counterpart.name_en),
            "isSynthetic": counterpart.is_synthetic,
        },
        "proposal": dict(bridge.proposal or {}),
        "syntheticDisclosureRequired": bridge.status == "synthetic_preview",
        "expiresAt": _iso(bridge.expires_at),
    }


def _relationship_for(a_id: str, b_id: str) -> MeetCampusRelationship:
    resident_a_id, resident_b_id = sorted((a_id, b_id))
    relationship = MeetCampusRelationship.query.filter_by(
        resident_a_id=resident_a_id,
        resident_b_id=resident_b_id,
    ).one_or_none()
    if relationship is None:
        relationship = MeetCampusRelationship(
            id=str(uuid.uuid4()),
            resident_a_id=resident_a_id,
            resident_b_id=resident_b_id,
            familiarity=0,
            trust=0,
            warmth=0,
            shared_interests=[],
            summary_zh="刚刚认识",
            summary_en="Just met",
        )
        db.session.add(relationship)
    return relationship


def _decision_interval(resident_id: str) -> timedelta:
    minimum = int(current_app.config.get("MEETCAMPUS_DECISION_MIN_MINUTES", 30))
    maximum = int(current_app.config.get("MEETCAMPUS_DECISION_MAX_MINUTES", 90))
    span = max(0, maximum - minimum)
    stable = sum(ord(char) for char in resident_id)
    return timedelta(minutes=minimum + (stable % (span + 1)))


def _period_for(hour: int) -> str:
    if hour < 12:
        return "morning"
    if hour < 18:
        return "afternoon"
    return "evening"


def _scene_from_schedule(
    resident: MeetCampusResident,
    now: datetime,
) -> MeetCampusScene | None:
    period = _period_for(now.hour)
    slug = next(
        (entry.get("scene") for entry in resident.schedule or [] if entry.get("period") == period),
        None,
    )
    if not slug:
        return None
    return MeetCampusScene.query.filter_by(
        world_id=resident.world_id,
        slug=slug,
        is_active=True,
    ).one_or_none()


def _observation(
    resident: MeetCampusResident,
    state: MeetCampusResidentState,
    scenes: list[MeetCampusScene],
) -> dict[str, Any]:
    scene = db.session.get(MeetCampusScene, state.scene_id)
    nearby = db.session.query(MeetCampusResident, MeetCampusResidentState).join(
        MeetCampusResidentState,
        MeetCampusResidentState.resident_id == MeetCampusResident.id,
    ).filter(
        MeetCampusResidentState.scene_id == state.scene_id,
        MeetCampusResident.id != resident.id,
        MeetCampusResident.is_active.is_(True),
    ).limit(5).all()
    return {
        "server_time": _iso(utc_now()),
        "current_scene": {
            "id": scene.id if scene else state.scene_id,
            "slug": scene.slug if scene else "unknown",
            "affordances": list(scene.affordances or []) if scene else [],
        },
        "available_scenes": [
            {"id": item.id, "slug": item.slug, "affordances": list(item.affordances or [])}
            for item in scenes
        ],
        "nearby_residents": [
            {"id": item.id, "name": item.name_en, "activity": item_state.activity}
            for item, item_state in nearby
        ],
        "needs": dict(state.needs or {}),
        "active_goal": dict(state.active_goal or {}),
    }


def _choose_decision(
    resident: MeetCampusResident,
    state: MeetCampusResidentState,
    scenes: list[MeetCampusScene],
    now: datetime,
) -> AgentDecision:
    proposal = propose_action(
        resident={
            "id": resident.id,
            "name": resident.name_en,
            "persona": dict(resident.persona or {}),
            "voice": dict(resident.voice or {}),
        },
        observation=_observation(resident, state, scenes),
    )
    if proposal is not None:
        return proposal
    scheduled = _scene_from_schedule(resident, now)
    if scheduled and scheduled.id != state.scene_id:
        return AgentDecision(
            action="move",
            scene_slug=scheduled.slug,
            affordance=None,
            target_resident_id=None,
            intention_zh=f"按自己的节奏去{scheduled.name_zh}",
            intention_en=f"Follow the routine to {scheduled.name_en}",
            source="schedule",
        )
    current_scene = db.session.get(MeetCampusScene, state.scene_id)
    affordance = (current_scene.affordances or ["observe"])[0] if current_scene else "observe"
    return AgentDecision(
        action="activity",
        scene_slug=current_scene.slug if current_scene else None,
        affordance=affordance,
        target_resident_id=None,
        intention_zh="继续眼前的校园生活",
        intention_en="Continue the current campus routine",
        source="schedule",
    )


def _create_event(
    *,
    resident: MeetCampusResident,
    scene: MeetCampusScene,
    decision: AgentDecision,
    participant_ids: list[str],
    now: datetime,
    slot_key: str,
) -> MeetCampusEvent | None:
    idempotency_key = f"{resident.id}:{slot_key}:{decision.action}:{scene.id}"
    if MeetCampusEvent.query.filter_by(idempotency_key=idempotency_key).one_or_none():
        return None
    is_shared = bool(participant_ids)
    affordance = decision.affordance or "explore"
    if is_shared:
        summary_zh = f"在{scene.name_zh}和一位居民一起体验了{affordance}。"
        summary_en = f"Shared {affordance} with another resident at {scene.name_en}."
        kind = "shared_activity"
        importance = 7
    elif decision.action == "move":
        summary_zh = f"沿着自己的计划来到了{scene.name_zh}。"
        summary_en = f"Arrived at {scene.name_en} as part of the day's plan."
        kind = "arrived"
        importance = 3
    else:
        summary_zh = f"在{scene.name_zh}体验了{affordance}。"
        summary_en = f"Spent time on {affordance} at {scene.name_en}."
        kind = "activity"
        importance = 4
    event = MeetCampusEvent(
        id=str(uuid.uuid4()),
        world_id=resident.world_id,
        scene_id=scene.id,
        actor_resident_id=resident.id,
        kind=kind,
        summary_zh=summary_zh,
        summary_en=summary_en,
        participant_resident_ids=[resident.id, *participant_ids],
        payload={
            "action": decision.action,
            "affordance": affordance,
            "intention": {"zh": decision.intention_zh, "en": decision.intention_en},
            "decisionSource": decision.source,
        },
        importance=importance,
        idempotency_key=idempotency_key,
        occurred_at=now,
    )
    db.session.add(event)
    db.session.flush()
    db.session.add(MeetCampusMemory(
        id=str(uuid.uuid4()),
        resident_id=resident.id,
        kind="episodic",
        content_zh=summary_zh,
        content_en=summary_en,
        source_event_ids=[event.id],
        source="lived_event",
        salience=importance,
        confidence=1.0,
    ))
    for participant_id in participant_ids:
        relationship = _relationship_for(resident.id, participant_id)
        relationship.familiarity = min(100, relationship.familiarity + 8)
        relationship.trust = min(100, relationship.trust + 3)
        relationship.warmth = min(100, relationship.warmth + 5)
        relationship.summary_zh = f"在{scene.name_zh}有过共同经历"
        relationship.summary_en = f"Shared an experience at {scene.name_en}"
        relationship.last_event_id = event.id
    return event


def _story_for_owner(
    event: MeetCampusEvent,
    owner_resident: MeetCampusResident,
) -> MeetCampusStory | None:
    if event.importance < 4 or owner_resident.owner_user_id is None:
        return None
    existing = MeetCampusStory.query.filter_by(
        owner_user_id=owner_resident.owner_user_id
    ).all()
    if any(event.id in (story.event_ids or []) for story in existing):
        return None
    narration = narrate_event(
        resident={
            "id": owner_resident.id,
            "name": owner_resident.name_en,
            "persona": dict(owner_resident.persona or {}),
            "voice": dict(owner_resident.voice or {}),
        },
        event={
            "id": event.id,
            "kind": event.kind,
            "summary_zh": event.summary_zh,
            "summary_en": event.summary_en,
            "scene_id": event.scene_id,
            "participant_resident_ids": list(event.participant_resident_ids or []),
            "occurred_at": _iso(event.occurred_at),
            "payload": dict(event.payload or {}),
        },
    ) or {
        "title_zh": "今天的一段见闻",
        "title_en": "Something from today",
        "narration_zh": event.summary_zh,
        "narration_en": event.summary_en,
    }
    story = MeetCampusStory(
        id=str(uuid.uuid4()),
        owner_user_id=owner_resident.owner_user_id,
        resident_id=owner_resident.id,
        title_zh=narration["title_zh"],
        title_en=narration["title_en"],
        narration_zh=narration["narration_zh"],
        narration_en=narration["narration_en"],
        event_ids=[event.id],
        bridge_candidate=event.kind == "shared_activity",
        is_viewed=False,
    )
    db.session.add(story)
    return story


def advance_world(
    now: datetime | None = None,
    *,
    max_residents: int | None = None,
) -> dict[str, Any]:
    """Advance due residents exactly once for the current decision slot."""
    if not current_app.config.get("MEETCAMPUS_WORLD_ENABLED", True):
        return {"status": "disabled", "advancedResidents": 0, "events": 0}
    now = now or utc_now()
    world = _world()
    scenes = MeetCampusScene.query.filter_by(world_id=world.id, is_active=True).all()
    scenes_by_slug = {scene.slug: scene for scene in scenes}
    limit = max_residents or int(
        current_app.config.get("MEETCAMPUS_MAX_DUE_RESIDENTS_PER_TICK", 8)
    )
    due = db.session.query(MeetCampusResident, MeetCampusResidentState).join(
        MeetCampusResidentState,
        MeetCampusResidentState.resident_id == MeetCampusResident.id,
    ).filter(
        MeetCampusResident.world_id == world.id,
        MeetCampusResident.is_active.is_(True),
        MeetCampusResidentState.next_decision_at <= now,
    ).order_by(MeetCampusResidentState.next_decision_at).limit(limit).all()
    created_events: list[MeetCampusEvent] = []
    for resident, state in due:
        pending = MeetCampusCommand.query.filter_by(
            resident_id=resident.id,
            status="pending",
        ).order_by(MeetCampusCommand.created_at).first()
        if pending:
            state.active_goal = {
                "commandId": pending.id,
                "kind": pending.kind,
                "text": pending.text,
                **dict(pending.payload or {}),
            }
        decision = _choose_decision(resident, state, scenes, now)
        if pending and pending.payload.get("targetSceneId"):
            commanded_scene = db.session.get(
                MeetCampusScene,
                pending.payload["targetSceneId"],
            )
            if commanded_scene:
                decision = AgentDecision(
                    action="move",
                    scene_slug=commanded_scene.slug,
                    affordance=None,
                    target_resident_id=None,
                    intention_zh=pending.text,
                    intention_en=pending.text,
                    source="owner_command",
                )
        target_scene = scenes_by_slug.get(decision.scene_slug or "")
        if target_scene is None:
            target_scene = db.session.get(MeetCampusScene, state.scene_id)
        if target_scene is None:
            state.next_decision_at = now + _decision_interval(resident.id)
            continue
        state.scene_id = target_scene.id
        state.position_x = float(
            18 + (sum(ord(c) for c in resident.id + str(world.state_version)) % 65)
        )
        state.position_y = float(
            18 + (sum(ord(c) for c in resident.slug + str(world.state_version)) % 65)
        )
        state.activity = decision.affordance or decision.action
        state.activity_started_at = now
        state.last_decision_at = now
        state.next_decision_at = now + _decision_interval(resident.id)

        colocated = db.session.query(MeetCampusResident).join(
            MeetCampusResidentState,
            MeetCampusResidentState.resident_id == MeetCampusResident.id,
        ).filter(
            MeetCampusResidentState.scene_id == target_scene.id,
            MeetCampusResident.id != resident.id,
            MeetCampusResident.is_active.is_(True),
        ).order_by(MeetCampusResident.id).all()
        participant_ids: list[str] = []
        if colocated and decision.action in {"talk", "activity"}:
            preferred = next(
                (item for item in colocated if item.id == decision.target_resident_id),
                None,
            )
            participant_ids = [(preferred or colocated[0]).id]
        slot_seconds = max(
            900,
            int(current_app.config.get("MEETCAMPUS_DECISION_MIN_MINUTES", 30)) * 60,
        )
        event = _create_event(
            resident=resident,
            scene=target_scene,
            decision=decision,
            participant_ids=participant_ids,
            now=now,
            slot_key=str(int(now.timestamp()) // slot_seconds),
        )
        if event:
            created_events.append(event)
            owner_candidate_ids = [resident.id, *participant_ids]
            for owner_resident in MeetCampusResident.query.filter(
                MeetCampusResident.id.in_(owner_candidate_ids),
                MeetCampusResident.owner_user_id.isnot(None),
            ).all():
                _story_for_owner(event, owner_resident)
        if pending:
            pending.status = "completed"
            pending.outcome = {
                "sceneId": target_scene.id,
                "activity": state.activity,
            }
            pending.resolved_at = now

    if due:
        world.state_version += 1
    world.last_advanced_at = now
    db.session.commit()
    return {
        "status": "advanced",
        "advancedResidents": len(due),
        "events": len(created_events),
        "stateVersion": world.state_version,
        "lastAdvancedAt": _iso(world.last_advanced_at),
    }


def world_worker_status() -> dict[str, Any]:
    world = _world()
    last_advanced = world.last_advanced_at
    if last_advanced.tzinfo is None:
        last_advanced = last_advanced.replace(tzinfo=timezone.utc)
    age_seconds = max(0, int((utc_now() - last_advanced).total_seconds()))
    tick = int(current_app.config.get("MEETCAMPUS_WORLD_TICK_SECONDS", 60))
    return {
        "status": "ok" if age_seconds <= tick * 3 else "delayed",
        "lastAdvancedAt": _iso(world.last_advanced_at),
        "ageSeconds": age_seconds,
        "stateVersion": world.state_version,
        "providerConfigured": bool(current_app.config.get("MEETCAMPUS_AI_API_KEY")),
    }
