"""Persistent MeetCampus world, resident, memory, and consent models."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, CheckConstraint, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB

from app.extensions import db


JsonType = JSON().with_variant(JSONB, "postgresql")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class MeetCampusWorld(db.Model):
    __tablename__ = "meetcampus_worlds"

    id = db.Column(db.String(36), primary_key=True)
    slug = db.Column(db.String(80), nullable=False, unique=True)
    name_zh = db.Column(db.String(120), nullable=False)
    name_en = db.Column(db.String(120), nullable=False)
    status = db.Column(db.String(24), nullable=False, default="active")
    seed_version = db.Column(db.String(32), nullable=False)
    state_version = db.Column(db.Integer, nullable=False, default=1)
    last_advanced_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)


class MeetCampusScene(db.Model):
    __tablename__ = "meetcampus_scenes"

    id = db.Column(db.String(36), primary_key=True)
    world_id = db.Column(db.String(36), db.ForeignKey("meetcampus_worlds.id", ondelete="CASCADE"), nullable=False)
    parent_scene_id = db.Column(db.String(36), db.ForeignKey("meetcampus_scenes.id", ondelete="SET NULL"))
    slug = db.Column(db.String(80), nullable=False)
    kind = db.Column(db.String(32), nullable=False)
    name_zh = db.Column(db.String(120), nullable=False)
    name_en = db.Column(db.String(120), nullable=False)
    map_x = db.Column(db.Float, nullable=False, default=50)
    map_y = db.Column(db.Float, nullable=False, default=50)
    affordances = db.Column(JsonType, nullable=False, default=list)
    visual = db.Column(JsonType, nullable=False, default=dict)
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    __table_args__ = (
        UniqueConstraint("world_id", "slug", name="uq_meetcampus_scene_world_slug"),
        CheckConstraint("map_x >= 0 AND map_x <= 100", name="ck_meetcampus_scene_map_x"),
        CheckConstraint("map_y >= 0 AND map_y <= 100", name="ck_meetcampus_scene_map_y"),
    )


class MeetCampusSceneConnection(db.Model):
    __tablename__ = "meetcampus_scene_connections"

    id = db.Column(db.String(36), primary_key=True)
    world_id = db.Column(db.String(36), db.ForeignKey("meetcampus_worlds.id", ondelete="CASCADE"), nullable=False)
    from_scene_id = db.Column(db.String(36), db.ForeignKey("meetcampus_scenes.id", ondelete="CASCADE"), nullable=False)
    to_scene_id = db.Column(db.String(36), db.ForeignKey("meetcampus_scenes.id", ondelete="CASCADE"), nullable=False)
    travel_minutes = db.Column(db.Integer, nullable=False, default=8)
    path = db.Column(JsonType, nullable=False, default=list)

    __table_args__ = (
        UniqueConstraint("from_scene_id", "to_scene_id", name="uq_meetcampus_scene_connection"),
        CheckConstraint("travel_minutes > 0", name="ck_meetcampus_connection_travel_minutes"),
    )


class MeetCampusResident(db.Model):
    __tablename__ = "meetcampus_residents"

    id = db.Column(db.String(36), primary_key=True)
    world_id = db.Column(db.String(36), db.ForeignKey("meetcampus_worlds.id", ondelete="CASCADE"), nullable=False)
    slug = db.Column(db.String(80), nullable=False)
    owner_user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), unique=True)
    is_synthetic = db.Column(db.Boolean, nullable=False, default=True)
    name_zh = db.Column(db.String(80), nullable=False)
    name_en = db.Column(db.String(80), nullable=False)
    pronouns = db.Column(JsonType, nullable=False, default=dict)
    persona = db.Column(JsonType, nullable=False, default=dict)
    appearance = db.Column(JsonType, nullable=False, default=dict)
    schedule = db.Column(JsonType, nullable=False, default=list)
    voice = db.Column(JsonType, nullable=False, default=dict)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)

    __table_args__ = (
        UniqueConstraint("world_id", "slug", name="uq_meetcampus_resident_world_slug"),
    )


class MeetCampusResidentState(db.Model):
    __tablename__ = "meetcampus_resident_states"

    resident_id = db.Column(db.String(36), db.ForeignKey("meetcampus_residents.id", ondelete="CASCADE"), primary_key=True)
    scene_id = db.Column(db.String(36), db.ForeignKey("meetcampus_scenes.id", ondelete="RESTRICT"), nullable=False)
    position_x = db.Column(db.Float, nullable=False, default=50)
    position_y = db.Column(db.Float, nullable=False, default=50)
    activity = db.Column(db.String(80), nullable=False, default="settling_in")
    activity_started_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
    needs = db.Column(JsonType, nullable=False, default=dict)
    active_goal = db.Column(JsonType, nullable=False, default=dict)
    next_decision_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
    last_decision_at = db.Column(db.DateTime(timezone=True))
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)

    __table_args__ = (
        CheckConstraint("position_x >= 0 AND position_x <= 100", name="ck_meetcampus_state_position_x"),
        CheckConstraint("position_y >= 0 AND position_y <= 100", name="ck_meetcampus_state_position_y"),
    )


class MeetCampusOwnerProfile(db.Model):
    __tablename__ = "meetcampus_owner_profiles"

    id = db.Column(db.Integer, primary_key=True)
    owner_user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True)
    resident_id = db.Column(db.String(36), db.ForeignKey("meetcampus_residents.id", ondelete="CASCADE"), nullable=False, unique=True)
    onboarding_status = db.Column(db.String(24), nullable=False, default="not_started")
    locale = db.Column(db.String(8), nullable=False, default="zh")
    autonomy_level = db.Column(db.String(24), nullable=False, default="balanced")
    anchors = db.Column(JsonType, nullable=False, default=dict)
    privacy_rules = db.Column(JsonType, nullable=False, default=dict)
    last_homecoming_at = db.Column(db.DateTime(timezone=True))
    completed_at = db.Column(db.DateTime(timezone=True))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)


class MeetCampusCommand(db.Model):
    __tablename__ = "meetcampus_commands"

    id = db.Column(db.String(36), primary_key=True)
    owner_user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    resident_id = db.Column(db.String(36), db.ForeignKey("meetcampus_residents.id", ondelete="CASCADE"), nullable=False)
    kind = db.Column(db.String(32), nullable=False)
    text = db.Column(db.Text, nullable=False)
    payload = db.Column(JsonType, nullable=False, default=dict)
    status = db.Column(db.String(24), nullable=False, default="pending")
    outcome = db.Column(JsonType, nullable=False, default=dict)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
    resolved_at = db.Column(db.DateTime(timezone=True))


class MeetCampusEvent(db.Model):
    __tablename__ = "meetcampus_events"

    id = db.Column(db.String(36), primary_key=True)
    world_id = db.Column(db.String(36), db.ForeignKey("meetcampus_worlds.id", ondelete="CASCADE"), nullable=False)
    scene_id = db.Column(db.String(36), db.ForeignKey("meetcampus_scenes.id", ondelete="RESTRICT"), nullable=False)
    actor_resident_id = db.Column(db.String(36), db.ForeignKey("meetcampus_residents.id", ondelete="CASCADE"), nullable=False)
    kind = db.Column(db.String(48), nullable=False)
    summary_zh = db.Column(db.Text, nullable=False)
    summary_en = db.Column(db.Text, nullable=False)
    participant_resident_ids = db.Column(JsonType, nullable=False, default=list)
    payload = db.Column(JsonType, nullable=False, default=dict)
    importance = db.Column(db.Integer, nullable=False, default=1)
    idempotency_key = db.Column(db.String(160), nullable=False, unique=True)
    occurred_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)

    __table_args__ = (
        CheckConstraint("importance >= 1 AND importance <= 10", name="ck_meetcampus_event_importance"),
    )


class MeetCampusMemory(db.Model):
    __tablename__ = "meetcampus_memories"

    id = db.Column(db.String(36), primary_key=True)
    resident_id = db.Column(db.String(36), db.ForeignKey("meetcampus_residents.id", ondelete="CASCADE"), nullable=False)
    kind = db.Column(db.String(32), nullable=False)
    content_zh = db.Column(db.Text, nullable=False)
    content_en = db.Column(db.Text, nullable=False)
    source_event_ids = db.Column(JsonType, nullable=False, default=list)
    source = db.Column(db.String(32), nullable=False, default="lived_event")
    salience = db.Column(db.Integer, nullable=False, default=1)
    confidence = db.Column(db.Float, nullable=False, default=1.0)
    superseded_by_id = db.Column(db.String(36), db.ForeignKey("meetcampus_memories.id", ondelete="SET NULL"))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)


class MeetCampusRelationship(db.Model):
    __tablename__ = "meetcampus_relationships"

    id = db.Column(db.String(36), primary_key=True)
    resident_a_id = db.Column(db.String(36), db.ForeignKey("meetcampus_residents.id", ondelete="CASCADE"), nullable=False)
    resident_b_id = db.Column(db.String(36), db.ForeignKey("meetcampus_residents.id", ondelete="CASCADE"), nullable=False)
    familiarity = db.Column(db.Integer, nullable=False, default=0)
    trust = db.Column(db.Integer, nullable=False, default=0)
    warmth = db.Column(db.Integer, nullable=False, default=0)
    shared_interests = db.Column(JsonType, nullable=False, default=list)
    summary_zh = db.Column(db.Text, nullable=False, default="")
    summary_en = db.Column(db.Text, nullable=False, default="")
    last_event_id = db.Column(db.String(36), db.ForeignKey("meetcampus_events.id", ondelete="SET NULL"))
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)

    __table_args__ = (
        UniqueConstraint("resident_a_id", "resident_b_id", name="uq_meetcampus_relationship_pair"),
        CheckConstraint("resident_a_id < resident_b_id", name="ck_meetcampus_relationship_order"),
    )


class MeetCampusStory(db.Model):
    __tablename__ = "meetcampus_stories"

    id = db.Column(db.String(36), primary_key=True)
    owner_user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    resident_id = db.Column(db.String(36), db.ForeignKey("meetcampus_residents.id", ondelete="CASCADE"), nullable=False)
    title_zh = db.Column(db.String(180), nullable=False)
    title_en = db.Column(db.String(180), nullable=False)
    narration_zh = db.Column(db.Text, nullable=False)
    narration_en = db.Column(db.Text, nullable=False)
    event_ids = db.Column(JsonType, nullable=False, default=list)
    bridge_candidate = db.Column(db.Boolean, nullable=False, default=False)
    is_viewed = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
    viewed_at = db.Column(db.DateTime(timezone=True))


class MeetCampusBridge(db.Model):
    __tablename__ = "meetcampus_bridges"

    id = db.Column(db.String(36), primary_key=True)
    source_event_id = db.Column(db.String(36), db.ForeignKey("meetcampus_events.id", ondelete="RESTRICT"), nullable=False)
    initiator_resident_id = db.Column(db.String(36), db.ForeignKey("meetcampus_residents.id", ondelete="CASCADE"), nullable=False)
    counterpart_resident_id = db.Column(db.String(36), db.ForeignKey("meetcampus_residents.id", ondelete="CASCADE"), nullable=False)
    initiator_owner_user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    counterpart_owner_user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    status = db.Column(db.String(32), nullable=False, default="initiator_pending")
    proposal = db.Column(JsonType, nullable=False, default=dict)
    initiator_consented_at = db.Column(db.DateTime(timezone=True))
    counterpart_consented_at = db.Column(db.DateTime(timezone=True))
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)


class MeetCampusAgentRun(db.Model):
    __tablename__ = "meetcampus_agent_runs"

    id = db.Column(db.String(36), primary_key=True)
    resident_id = db.Column(db.String(36), db.ForeignKey("meetcampus_residents.id", ondelete="SET NULL"))
    operation = db.Column(db.String(40), nullable=False)
    provider = db.Column(db.String(40), nullable=False, default="hkust_aigw")
    model = db.Column(db.String(80), nullable=False)
    status = db.Column(db.String(24), nullable=False)
    prompt_hash = db.Column(db.String(64), nullable=False)
    input_tokens = db.Column(db.Integer)
    output_tokens = db.Column(db.Integer)
    latency_ms = db.Column(db.Integer)
    error_code = db.Column(db.String(80))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)


class MeetCampusActivityDefinition(db.Model):
    """A world-owned activity residents may attempt at one scene."""

    __tablename__ = "meetcampus_activity_definitions"

    id = db.Column(db.String(36), primary_key=True)
    world_id = db.Column(db.String(36), db.ForeignKey("meetcampus_worlds.id", ondelete="CASCADE"), nullable=False)
    scene_id = db.Column(db.String(36), db.ForeignKey("meetcampus_scenes.id", ondelete="CASCADE"), nullable=False)
    slug = db.Column(db.String(80), nullable=False)
    name_zh = db.Column(db.String(120), nullable=False)
    name_en = db.Column(db.String(120), nullable=False)
    description_zh = db.Column(db.Text, nullable=False, default="")
    description_en = db.Column(db.Text, nullable=False, default="")
    min_participants = db.Column(db.Integer, nullable=False, default=1)
    max_participants = db.Column(db.Integer, nullable=False, default=1)
    duration_min_minutes = db.Column(db.Integer, nullable=False, default=10)
    duration_max_minutes = db.Column(db.Integer, nullable=False, default=30)
    capacity = db.Column(db.Integer, nullable=False, default=8)
    requirements = db.Column(JsonType, nullable=False, default=dict)
    effects = db.Column(JsonType, nullable=False, default=dict)
    outcome_rules = db.Column(JsonType, nullable=False, default=dict)
    tags = db.Column(JsonType, nullable=False, default=list)
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    __table_args__ = (
        UniqueConstraint("scene_id", "slug", name="uq_meetcampus_activity_scene_slug"),
        CheckConstraint("min_participants > 0", name="ck_meetcampus_activity_min_participants"),
        CheckConstraint("max_participants >= min_participants", name="ck_meetcampus_activity_max_participants"),
        CheckConstraint("duration_min_minutes > 0", name="ck_meetcampus_activity_min_duration"),
        CheckConstraint("duration_max_minutes >= duration_min_minutes", name="ck_meetcampus_activity_max_duration"),
    )


class MeetCampusJourney(db.Model):
    """A time-bounded traversal whose progress can be rendered continuously."""

    __tablename__ = "meetcampus_journeys"

    id = db.Column(db.String(36), primary_key=True)
    world_id = db.Column(db.String(36), db.ForeignKey("meetcampus_worlds.id", ondelete="CASCADE"), nullable=False)
    resident_id = db.Column(db.String(36), db.ForeignKey("meetcampus_residents.id", ondelete="CASCADE"), nullable=False)
    from_scene_id = db.Column(db.String(36), db.ForeignKey("meetcampus_scenes.id", ondelete="RESTRICT"), nullable=False)
    to_scene_id = db.Column(db.String(36), db.ForeignKey("meetcampus_scenes.id", ondelete="RESTRICT"), nullable=False)
    route_scene_ids = db.Column(JsonType, nullable=False, default=list)
    path = db.Column(JsonType, nullable=False, default=list)
    status = db.Column(db.String(24), nullable=False, default="traveling")
    intention = db.Column(JsonType, nullable=False, default=dict)
    decision_id = db.Column(db.String(36), db.ForeignKey("meetcampus_decisions.id", ondelete="SET NULL"))
    command_id = db.Column(db.String(36), db.ForeignKey("meetcampus_commands.id", ondelete="SET NULL"))
    depart_at = db.Column(db.DateTime(timezone=True), nullable=False)
    arrive_at = db.Column(db.DateTime(timezone=True), nullable=False)
    completed_at = db.Column(db.DateTime(timezone=True))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)

    __table_args__ = (
        CheckConstraint("arrive_at > depart_at", name="ck_meetcampus_journey_time_order"),
    )


class MeetCampusActivitySession(db.Model):
    """A world-validated attempt to perform an activity with explicit membership."""

    __tablename__ = "meetcampus_activity_sessions"

    id = db.Column(db.String(36), primary_key=True)
    world_id = db.Column(db.String(36), db.ForeignKey("meetcampus_worlds.id", ondelete="CASCADE"), nullable=False)
    activity_definition_id = db.Column(db.String(36), db.ForeignKey("meetcampus_activity_definitions.id", ondelete="RESTRICT"), nullable=False)
    scene_id = db.Column(db.String(36), db.ForeignKey("meetcampus_scenes.id", ondelete="RESTRICT"), nullable=False)
    host_resident_id = db.Column(db.String(36), db.ForeignKey("meetcampus_residents.id", ondelete="CASCADE"), nullable=False)
    status = db.Column(db.String(24), nullable=False, default="forming")
    seed = db.Column(db.Integer, nullable=False)
    intention = db.Column(JsonType, nullable=False, default=dict)
    result = db.Column(JsonType, nullable=False, default=dict)
    starts_at = db.Column(db.DateTime(timezone=True))
    ends_at = db.Column(db.DateTime(timezone=True))
    completed_at = db.Column(db.DateTime(timezone=True))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)


class MeetCampusActivityParticipant(db.Model):
    __tablename__ = "meetcampus_activity_participants"

    id = db.Column(db.String(36), primary_key=True)
    session_id = db.Column(db.String(36), db.ForeignKey("meetcampus_activity_sessions.id", ondelete="CASCADE"), nullable=False)
    resident_id = db.Column(db.String(36), db.ForeignKey("meetcampus_residents.id", ondelete="CASCADE"), nullable=False)
    role = db.Column(db.String(24), nullable=False, default="participant")
    status = db.Column(db.String(24), nullable=False, default="invited")
    response_reason = db.Column(JsonType, nullable=False, default=dict)
    outcome = db.Column(JsonType, nullable=False, default=dict)
    invited_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
    responded_at = db.Column(db.DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("session_id", "resident_id", name="uq_meetcampus_activity_participant"),
    )


class MeetCampusObservation(db.Model):
    __tablename__ = "meetcampus_observations"

    id = db.Column(db.String(36), primary_key=True)
    resident_id = db.Column(db.String(36), db.ForeignKey("meetcampus_residents.id", ondelete="CASCADE"), nullable=False)
    world_state_version = db.Column(db.Integer, nullable=False)
    payload = db.Column(JsonType, nullable=False, default=dict)
    observed_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)


class MeetCampusDecision(db.Model):
    __tablename__ = "meetcampus_decisions"

    id = db.Column(db.String(36), primary_key=True)
    resident_id = db.Column(db.String(36), db.ForeignKey("meetcampus_residents.id", ondelete="CASCADE"), nullable=False)
    observation_id = db.Column(db.String(36), db.ForeignKey("meetcampus_observations.id", ondelete="RESTRICT"), nullable=False)
    candidates = db.Column(JsonType, nullable=False, default=list)
    selected_intent = db.Column(JsonType, nullable=False, default=dict)
    source = db.Column(db.String(32), nullable=False)
    status = db.Column(db.String(24), nullable=False, default="proposed")
    validation = db.Column(JsonType, nullable=False, default=dict)
    execution = db.Column(JsonType, nullable=False, default=dict)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
    executed_at = db.Column(db.DateTime(timezone=True))


class MeetCampusResidentPlan(db.Model):
    __tablename__ = "meetcampus_resident_plans"

    id = db.Column(db.String(36), primary_key=True)
    resident_id = db.Column(db.String(36), db.ForeignKey("meetcampus_residents.id", ondelete="CASCADE"), nullable=False)
    plan_date = db.Column(db.Date, nullable=False)
    goals = db.Column(JsonType, nullable=False, default=list)
    items = db.Column(JsonType, nullable=False, default=list)
    source = db.Column(db.String(32), nullable=False, default="routine")
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)

    __table_args__ = (
        UniqueConstraint("resident_id", "plan_date", name="uq_meetcampus_resident_plan_date"),
    )
