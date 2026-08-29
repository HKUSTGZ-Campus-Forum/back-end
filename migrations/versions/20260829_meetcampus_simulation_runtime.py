"""Add the authoritative MeetCampus simulation runtime.

Revision ID: 20260829_meetcampus_runtime
Revises: 20260828_home_carousel
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260829_meetcampus_runtime"
down_revision = "20260828_home_carousel"
branch_labels = None
depends_on = None

expected_seed_counts = {"public.meetcampus_activity_definitions": 21}

json_type = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")
WORLD_ID = "mc-world-campus-v1"


ACTIVITIES = [
    ("library-study", "mc-scene-library", "study", "专注自习", "Focused study", 1, 1, 20, 50, ["study", "quiet"], {"energy": -8, "focus": 14}, {}),
    ("library-read", "mc-scene-library", "read", "随手阅读", "Casual reading", 1, 1, 15, 35, ["reading", "quiet"], {"energy": -3, "focus": 8}, {}),
    ("library-chat", "mc-scene-library", "quiet_chat", "轻声聊聊", "Quiet chat", 2, 2, 8, 18, ["social", "conversation"], {"social": 10, "energy": -3}, {"kind": "conversation"}),
    ("gym-badminton", "mc-scene-gym", "badminton", "打一局羽毛球", "Play badminton", 2, 2, 12, 24, ["sport", "badminton", "social"], {"energy": -18, "social": 9}, {"kind": "competitive", "skill": "badminton"}),
    ("gym-basketball", "mc-scene-gym", "basketball", "投一会儿篮", "Shoot hoops", 1, 4, 12, 28, ["sport", "basketball"], {"energy": -16, "social": 6}, {"kind": "score"}),
    ("gym-watch", "mc-scene-gym", "watch_game", "看一场比赛", "Watch a game", 1, 4, 10, 25, ["sport", "observe"], {"energy": 2, "novelty": 5}, {}),
    ("teaching-class", "mc-scene-teaching", "attend_class", "旁听一节课", "Attend a class", 1, 1, 25, 50, ["study", "class"], {"energy": -8, "focus": 12}, {}),
    ("teaching-study", "mc-scene-teaching", "study", "整理课堂笔记", "Review notes", 1, 1, 15, 35, ["study", "quiet"], {"energy": -5, "focus": 10}, {}),
    ("college-rest", "mc-scene-college", "rest", "回书院歇一会儿", "Rest at the college", 1, 1, 12, 30, ["rest"], {"energy": 18, "stress": -10}, {}),
    ("college-chat", "mc-scene-college", "chat", "在公共空间聊天", "Chat in the common area", 2, 3, 8, 20, ["social", "conversation"], {"social": 10, "stress": -4}, {"kind": "conversation"}),
    ("canteen-eat", "mc-scene-canteen", "eat", "好好吃一顿", "Have a meal", 1, 1, 12, 25, ["food", "rest"], {"hunger": -35, "energy": 10}, {}),
    ("canteen-share", "mc-scene-canteen", "share_food", "和别人拼桌", "Share a table", 2, 3, 15, 28, ["food", "social"], {"hunger": -32, "social": 8}, {"kind": "conversation"}),
    ("center-workshop", "mc-scene-student-center", "workshop", "参加工作坊", "Join a workshop", 1, 4, 20, 45, ["creative", "social"], {"energy": -8, "novelty": 12}, {"kind": "collaborative"}),
    ("center-exhibition", "mc-scene-student-center", "exhibition", "逛一场展览", "Explore an exhibition", 1, 2, 12, 30, ["art", "observe"], {"novelty": 11, "energy": -3}, {}),
    ("lab-research", "mc-scene-lab", "research", "推进研究想法", "Work on a research idea", 1, 1, 20, 50, ["research", "study"], {"focus": 13, "energy": -10}, {}),
    ("lab-build", "mc-scene-lab", "build", "动手做点东西", "Build something", 1, 2, 20, 45, ["creative", "research"], {"focus": 10, "novelty": 8}, {"kind": "collaborative"}),
    ("lakeside-walk", "mc-scene-lakeside", "walk", "沿湖散步", "Walk by the lake", 1, 2, 10, 24, ["outdoor", "walk"], {"stress": -12, "energy": -4}, {}),
    ("lakeside-reflect", "mc-scene-lakeside", "reflect", "坐下来想想", "Sit and reflect", 1, 1, 10, 22, ["outdoor", "quiet"], {"stress": -10, "focus": 5}, {}),
    ("hall-badminton", "mc-scene-gym-badminton", "badminton", "认真打一局羽毛球", "Play a badminton match", 2, 2, 14, 26, ["sport", "badminton", "social"], {"energy": -20, "social": 10}, {"kind": "competitive", "skill": "badminton"}),
    ("reading-room", "mc-scene-library-reading", "read", "在阅览区读书", "Read in the reading room", 1, 1, 18, 40, ["reading", "quiet"], {"focus": 10, "energy": -4}, {}),
    ("lounge-board-game", "mc-scene-college-lounge", "board_game", "玩一局桌游", "Play a board game", 2, 4, 15, 35, ["game", "social"], {"social": 12, "stress": -6}, {"kind": "competitive", "skill": "board_game"}),
]


def upgrade():
    with op.batch_alter_table("meetcampus_scene_connections") as batch:
        batch.add_column(sa.Column("path", json_type, nullable=False, server_default=sa.text("'[]'")))

    op.create_table(
        "meetcampus_activity_definitions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("world_id", sa.String(36), sa.ForeignKey("meetcampus_worlds.id", ondelete="CASCADE"), nullable=False),
        sa.Column("scene_id", sa.String(36), sa.ForeignKey("meetcampus_scenes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("slug", sa.String(80), nullable=False),
        sa.Column("name_zh", sa.String(120), nullable=False),
        sa.Column("name_en", sa.String(120), nullable=False),
        sa.Column("description_zh", sa.Text(), nullable=False),
        sa.Column("description_en", sa.Text(), nullable=False),
        sa.Column("min_participants", sa.Integer(), nullable=False),
        sa.Column("max_participants", sa.Integer(), nullable=False),
        sa.Column("duration_min_minutes", sa.Integer(), nullable=False),
        sa.Column("duration_max_minutes", sa.Integer(), nullable=False),
        sa.Column("capacity", sa.Integer(), nullable=False),
        sa.Column("requirements", json_type, nullable=False),
        sa.Column("effects", json_type, nullable=False),
        sa.Column("outcome_rules", json_type, nullable=False),
        sa.Column("tags", json_type, nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("scene_id", "slug", name="uq_meetcampus_activity_scene_slug"),
        sa.CheckConstraint("min_participants > 0", name="ck_meetcampus_activity_min_participants"),
        sa.CheckConstraint("max_participants >= min_participants", name="ck_meetcampus_activity_max_participants"),
        sa.CheckConstraint("duration_min_minutes > 0", name="ck_meetcampus_activity_min_duration"),
        sa.CheckConstraint("duration_max_minutes >= duration_min_minutes", name="ck_meetcampus_activity_max_duration"),
    )
    op.create_index("ix_meetcampus_activity_scene", "meetcampus_activity_definitions", ["scene_id", "is_active"])

    op.create_table(
        "meetcampus_observations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("resident_id", sa.String(36), sa.ForeignKey("meetcampus_residents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("world_state_version", sa.Integer(), nullable=False),
        sa.Column("payload", json_type, nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_meetcampus_observation_resident_time", "meetcampus_observations", ["resident_id", "observed_at"])

    op.create_table(
        "meetcampus_decisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("resident_id", sa.String(36), sa.ForeignKey("meetcampus_residents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("observation_id", sa.String(36), sa.ForeignKey("meetcampus_observations.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("candidates", json_type, nullable=False),
        sa.Column("selected_intent", json_type, nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("validation", json_type, nullable=False),
        sa.Column("execution", json_type, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("executed_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_meetcampus_decision_resident_time", "meetcampus_decisions", ["resident_id", "created_at"])

    op.create_table(
        "meetcampus_journeys",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("world_id", sa.String(36), sa.ForeignKey("meetcampus_worlds.id", ondelete="CASCADE"), nullable=False),
        sa.Column("resident_id", sa.String(36), sa.ForeignKey("meetcampus_residents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("from_scene_id", sa.String(36), sa.ForeignKey("meetcampus_scenes.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("to_scene_id", sa.String(36), sa.ForeignKey("meetcampus_scenes.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("route_scene_ids", json_type, nullable=False),
        sa.Column("path", json_type, nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("intention", json_type, nullable=False),
        sa.Column("decision_id", sa.String(36), sa.ForeignKey("meetcampus_decisions.id", ondelete="SET NULL")),
        sa.Column("command_id", sa.String(36), sa.ForeignKey("meetcampus_commands.id", ondelete="SET NULL")),
        sa.Column("depart_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("arrive_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("arrive_at > depart_at", name="ck_meetcampus_journey_time_order"),
    )
    op.create_index("ix_meetcampus_journey_due", "meetcampus_journeys", ["status", "arrive_at"])
    op.create_index("ix_meetcampus_journey_resident", "meetcampus_journeys", ["resident_id", "created_at"])

    op.create_table(
        "meetcampus_activity_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("world_id", sa.String(36), sa.ForeignKey("meetcampus_worlds.id", ondelete="CASCADE"), nullable=False),
        sa.Column("activity_definition_id", sa.String(36), sa.ForeignKey("meetcampus_activity_definitions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("scene_id", sa.String(36), sa.ForeignKey("meetcampus_scenes.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("host_resident_id", sa.String(36), sa.ForeignKey("meetcampus_residents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("seed", sa.Integer(), nullable=False),
        sa.Column("intention", json_type, nullable=False),
        sa.Column("result", json_type, nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True)),
        sa.Column("ends_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_meetcampus_session_due", "meetcampus_activity_sessions", ["status", "ends_at"])

    op.create_table(
        "meetcampus_activity_participants",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("session_id", sa.String(36), sa.ForeignKey("meetcampus_activity_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("resident_id", sa.String(36), sa.ForeignKey("meetcampus_residents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(24), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("response_reason", json_type, nullable=False),
        sa.Column("outcome", json_type, nullable=False),
        sa.Column("invited_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("responded_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("session_id", "resident_id", name="uq_meetcampus_activity_participant"),
    )
    op.create_index("ix_meetcampus_participant_pending", "meetcampus_activity_participants", ["resident_id", "status"])

    op.create_table(
        "meetcampus_resident_plans",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("resident_id", sa.String(36), sa.ForeignKey("meetcampus_residents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("plan_date", sa.Date(), nullable=False),
        sa.Column("goals", json_type, nullable=False),
        sa.Column("items", json_type, nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("resident_id", "plan_date", name="uq_meetcampus_resident_plan_date"),
    )

    activity_table = sa.table(
        "meetcampus_activity_definitions",
        sa.column("id"), sa.column("world_id"), sa.column("scene_id"), sa.column("slug"),
        sa.column("name_zh"), sa.column("name_en"), sa.column("description_zh"), sa.column("description_en"),
        sa.column("min_participants"), sa.column("max_participants"), sa.column("duration_min_minutes"),
        sa.column("duration_max_minutes"), sa.column("capacity"), sa.column("requirements", json_type),
        sa.column("effects", json_type), sa.column("outcome_rules", json_type), sa.column("tags", json_type),
        sa.column("is_active"),
    )
    op.bulk_insert(activity_table, [{
        "id": f"mc-activity-{key}", "world_id": WORLD_ID, "scene_id": scene_id, "slug": slug,
        "name_zh": zh, "name_en": en, "description_zh": zh, "description_en": en,
        "min_participants": minimum, "max_participants": maximum,
        "duration_min_minutes": duration_min, "duration_max_minutes": duration_max,
        "capacity": max(4, maximum * 2), "requirements": {}, "effects": effects,
        "outcome_rules": rules, "tags": tags, "is_active": True,
    } for key, scene_id, slug, zh, en, minimum, maximum, duration_min, duration_max, tags, effects, rules in ACTIVITIES])

    op.execute(sa.text("UPDATE meetcampus_worlds SET seed_version = 'private-beta-runtime-2' WHERE id = :world_id").bindparams(world_id=WORLD_ID))


def downgrade():
    op.execute(sa.text("UPDATE meetcampus_worlds SET seed_version = 'private-beta-1' WHERE id = :world_id").bindparams(world_id=WORLD_ID))
    op.drop_table("meetcampus_resident_plans")
    op.drop_index("ix_meetcampus_participant_pending", table_name="meetcampus_activity_participants")
    op.drop_table("meetcampus_activity_participants")
    op.drop_index("ix_meetcampus_session_due", table_name="meetcampus_activity_sessions")
    op.drop_table("meetcampus_activity_sessions")
    op.drop_index("ix_meetcampus_journey_resident", table_name="meetcampus_journeys")
    op.drop_index("ix_meetcampus_journey_due", table_name="meetcampus_journeys")
    op.drop_table("meetcampus_journeys")
    op.drop_index("ix_meetcampus_decision_resident_time", table_name="meetcampus_decisions")
    op.drop_table("meetcampus_decisions")
    op.drop_index("ix_meetcampus_observation_resident_time", table_name="meetcampus_observations")
    op.drop_table("meetcampus_observations")
    op.drop_index("ix_meetcampus_activity_scene", table_name="meetcampus_activity_definitions")
    op.drop_table("meetcampus_activity_definitions")
    with op.batch_alter_table("meetcampus_scene_connections") as batch:
        batch.drop_column("path")
