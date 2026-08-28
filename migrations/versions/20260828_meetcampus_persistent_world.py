"""add persistent MeetCampus world

Revision ID: 20260828_meetcampus_world
Revises: 20260824_section_label_255
Create Date: 2026-08-28
"""

from datetime import datetime, timedelta, timezone

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260828_meetcampus_world"
down_revision = "20260824_section_label_255"
branch_labels = None
depends_on = None

# The school release verifier accepts non-empty target-only tables only when a
# migration declares exact product-seed counts. Any drift still blocks release.
expected_seed_counts = {
    "public.meetcampus_worlds": 1,
    "public.meetcampus_scenes": 12,
    "public.meetcampus_scene_connections": 22,
    "public.meetcampus_residents": 20,
    "public.meetcampus_resident_states": 20,
}


json_type = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


WORLD_ID = "mc-world-campus-v1"


SCENES = [
    ("mc-scene-campus", None, "campus", "campus", "主校园", "Main Campus", 50, 50, ["walk", "observe"]),
    ("mc-scene-library", "mc-scene-campus", "library", "study", "图书馆", "Library", 23, 22, ["study", "read", "quiet_chat"]),
    ("mc-scene-gym", "mc-scene-campus", "gym", "sport", "体育馆", "Gym", 58, 20, ["badminton", "basketball", "watch_game"]),
    ("mc-scene-teaching", "mc-scene-campus", "teaching-building", "study", "教学楼", "Teaching Building", 30, 53, ["attend_class", "study", "walk"]),
    ("mc-scene-college", "mc-scene-campus", "college", "home", "书院", "College", 76, 28, ["rest", "chat", "walk"]),
    ("mc-scene-canteen", "mc-scene-campus", "canteen", "dining", "银杏食堂", "Ginkgo Canteen", 52, 61, ["eat", "share_food", "chat"]),
    ("mc-scene-student-center", "mc-scene-campus", "student-center", "activity", "学生活动中心", "Student Center", 78, 55, ["workshop", "exhibition", "chat"]),
    ("mc-scene-lab", "mc-scene-campus", "innovation-lab", "study", "创新实验室", "Innovation Lab", 75, 78, ["research", "build", "quiet_chat"]),
    ("mc-scene-lakeside", "mc-scene-campus", "lakeside", "outdoor", "湖畔步道", "Lakeside Walk", 51, 84, ["walk", "sit", "reflect"]),
    ("mc-scene-gym-badminton", "mc-scene-gym", "badminton-court", "sport", "羽毛球馆", "Badminton Hall", 50, 50, ["badminton", "watch_game", "chat"]),
    ("mc-scene-library-reading", "mc-scene-library", "reading-room", "study", "阅览区", "Reading Room", 50, 50, ["study", "read", "quiet_chat"]),
    ("mc-scene-college-lounge", "mc-scene-college", "college-lounge", "home", "书院公共空间", "College Lounge", 50, 50, ["rest", "board_game", "chat"]),
]


RESIDENTS = [
    ("mc-resident-mount", "mount", False, "小满", "Mori", ["羽毛球", "研究", "校园散步"], "curious", "balanced", "navy"),
    ("mc-resident-01", "zhou-yuan", True, "予安", "Yuan", ["安静自习", "散步", "咖啡"], "observant", "gentle", "sky"),
    ("mc-resident-02", "tang-ke", True, "可可", "Koko", ["机器人", "新口味", "摄影"], "inventive", "warm", "orange"),
    ("mc-resident-03", "chen-mo", True, "阿默", "Mo", ["设计", "展览", "拼贴"], "reflective", "measured", "violet"),
    ("mc-resident-04", "lin-xi", True, "林夕", "Lin", ["羽毛球", "夜跑", "音乐"], "energetic", "direct", "green"),
    ("mc-resident-05", "qiao-qiao", True, "乔乔", "Jo", ["数学", "桌游", "奶茶"], "playful", "precise", "pink"),
    ("mc-resident-06", "xu-zhi", True, "知知", "Zhi", ["阅读", "语言", "湖边"], "quiet", "thoughtful", "indigo"),
    ("mc-resident-07", "luo-yi", True, "洛一", "Luo", ["篮球", "编程", "电影"], "steady", "dry_humor", "teal"),
    ("mc-resident-08", "an-ran", True, "安然", "An", ["植物", "散步", "甜点"], "calm", "kind", "mint"),
    ("mc-resident-09", "yu-zhou", True, "宇宙", "Cosmo", ["天文", "科幻", "摄影"], "imaginative", "animated", "blue"),
    ("mc-resident-10", "wen-xin", True, "文心", "Wen", ["写作", "戏剧", "咖啡"], "expressive", "tactful", "amber"),
    ("mc-resident-11", "hai-tang", True, "海棠", "Hai", ["游泳", "生态", "志愿活动"], "open", "supportive", "coral"),
    ("mc-resident-12", "mu-zi", True, "木子", "Mu", ["硬件", "骑行", "面食"], "practical", "concise", "brown"),
    ("mc-resident-13", "qing-he", True, "清和", "Qing", ["古典乐", "历史", "书法"], "patient", "formal", "slate"),
    ("mc-resident-14", "fei-fei", True, "菲菲", "Faye", ["舞蹈", "短视频", "活动策划"], "social", "bright", "red"),
    ("mc-resident-15", "bei-bei", True, "贝贝", "Bei", ["经济学", "跑步", "播客"], "analytical", "friendly", "cyan"),
    ("mc-resident-16", "nan-xing", True, "南星", "Nan", ["化学", "烘焙", "推理小说"], "careful", "wry", "yellow"),
    ("mc-resident-17", "shi-yu", True, "时雨", "Rain", ["AI", "乒乓球", "电子乐"], "curious", "fast", "purple"),
    ("mc-resident-18", "zhi-xia", True, "知夏", "Summer", ["建筑", "旅行", "手绘"], "attentive", "gentle", "peach"),
    ("mc-resident-19", "he-chuan", True, "河川", "River", ["地理", "徒步", "纪录片"], "grounded", "reflective", "forest"),
]


def upgrade():
    op.create_table(
        "meetcampus_worlds",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("slug", sa.String(length=80), nullable=False, unique=True),
        sa.Column("name_zh", sa.String(length=120), nullable=False),
        sa.Column("name_en", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("seed_version", sa.String(length=32), nullable=False),
        sa.Column("state_version", sa.Integer(), nullable=False),
        sa.Column("last_advanced_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "meetcampus_scenes",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("world_id", sa.String(length=36), sa.ForeignKey("meetcampus_worlds.id", ondelete="CASCADE"), nullable=False),
        sa.Column("parent_scene_id", sa.String(length=36), sa.ForeignKey("meetcampus_scenes.id", ondelete="SET NULL")),
        sa.Column("slug", sa.String(length=80), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("name_zh", sa.String(length=120), nullable=False),
        sa.Column("name_en", sa.String(length=120), nullable=False),
        sa.Column("map_x", sa.Float(), nullable=False),
        sa.Column("map_y", sa.Float(), nullable=False),
        sa.Column("affordances", json_type, nullable=False),
        sa.Column("visual", json_type, nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("world_id", "slug", name="uq_meetcampus_scene_world_slug"),
        sa.CheckConstraint("map_x >= 0 AND map_x <= 100", name="ck_meetcampus_scene_map_x"),
        sa.CheckConstraint("map_y >= 0 AND map_y <= 100", name="ck_meetcampus_scene_map_y"),
    )
    op.create_table(
        "meetcampus_scene_connections",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("world_id", sa.String(length=36), sa.ForeignKey("meetcampus_worlds.id", ondelete="CASCADE"), nullable=False),
        sa.Column("from_scene_id", sa.String(length=36), sa.ForeignKey("meetcampus_scenes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("to_scene_id", sa.String(length=36), sa.ForeignKey("meetcampus_scenes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("travel_minutes", sa.Integer(), nullable=False),
        sa.UniqueConstraint("from_scene_id", "to_scene_id", name="uq_meetcampus_scene_connection"),
        sa.CheckConstraint("travel_minutes > 0", name="ck_meetcampus_connection_travel_minutes"),
    )
    op.create_table(
        "meetcampus_residents",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("world_id", sa.String(length=36), sa.ForeignKey("meetcampus_worlds.id", ondelete="CASCADE"), nullable=False),
        sa.Column("slug", sa.String(length=80), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), unique=True),
        sa.Column("is_synthetic", sa.Boolean(), nullable=False),
        sa.Column("name_zh", sa.String(length=80), nullable=False),
        sa.Column("name_en", sa.String(length=80), nullable=False),
        sa.Column("pronouns", json_type, nullable=False),
        sa.Column("persona", json_type, nullable=False),
        sa.Column("appearance", json_type, nullable=False),
        sa.Column("schedule", json_type, nullable=False),
        sa.Column("voice", json_type, nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("world_id", "slug", name="uq_meetcampus_resident_world_slug"),
    )
    op.create_index("ix_meetcampus_residents_world", "meetcampus_residents", ["world_id"])
    op.create_table(
        "meetcampus_resident_states",
        sa.Column("resident_id", sa.String(length=36), sa.ForeignKey("meetcampus_residents.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("scene_id", sa.String(length=36), sa.ForeignKey("meetcampus_scenes.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("position_x", sa.Float(), nullable=False),
        sa.Column("position_y", sa.Float(), nullable=False),
        sa.Column("activity", sa.String(length=80), nullable=False),
        sa.Column("activity_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("needs", json_type, nullable=False),
        sa.Column("active_goal", json_type, nullable=False),
        sa.Column("next_decision_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_decision_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("position_x >= 0 AND position_x <= 100", name="ck_meetcampus_state_position_x"),
        sa.CheckConstraint("position_y >= 0 AND position_y <= 100", name="ck_meetcampus_state_position_y"),
    )
    op.create_index("ix_meetcampus_state_due", "meetcampus_resident_states", ["next_decision_at"])
    op.create_table(
        "meetcampus_owner_profiles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("resident_id", sa.String(length=36), sa.ForeignKey("meetcampus_residents.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("onboarding_status", sa.String(length=24), nullable=False),
        sa.Column("locale", sa.String(length=8), nullable=False),
        sa.Column("autonomy_level", sa.String(length=24), nullable=False),
        sa.Column("anchors", json_type, nullable=False),
        sa.Column("privacy_rules", json_type, nullable=False),
        sa.Column("last_homecoming_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "meetcampus_commands",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("owner_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("resident_id", sa.String(length=36), sa.ForeignKey("meetcampus_residents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("payload", json_type, nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("outcome", json_type, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_meetcampus_commands_pending", "meetcampus_commands", ["resident_id", "status"])
    op.create_table(
        "meetcampus_events",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("world_id", sa.String(length=36), sa.ForeignKey("meetcampus_worlds.id", ondelete="CASCADE"), nullable=False),
        sa.Column("scene_id", sa.String(length=36), sa.ForeignKey("meetcampus_scenes.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("actor_resident_id", sa.String(length=36), sa.ForeignKey("meetcampus_residents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(length=48), nullable=False),
        sa.Column("summary_zh", sa.Text(), nullable=False),
        sa.Column("summary_en", sa.Text(), nullable=False),
        sa.Column("participant_resident_ids", json_type, nullable=False),
        sa.Column("payload", json_type, nullable=False),
        sa.Column("importance", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=160), nullable=False, unique=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("importance >= 1 AND importance <= 10", name="ck_meetcampus_event_importance"),
    )
    op.create_index("ix_meetcampus_events_resident_time", "meetcampus_events", ["actor_resident_id", "occurred_at"])
    op.create_table(
        "meetcampus_memories",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("resident_id", sa.String(length=36), sa.ForeignKey("meetcampus_residents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("content_zh", sa.Text(), nullable=False),
        sa.Column("content_en", sa.Text(), nullable=False),
        sa.Column("source_event_ids", json_type, nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("salience", sa.Integer(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("superseded_by_id", sa.String(length=36), sa.ForeignKey("meetcampus_memories.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_meetcampus_memories_resident", "meetcampus_memories", ["resident_id", "created_at"])
    op.create_table(
        "meetcampus_relationships",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("resident_a_id", sa.String(length=36), sa.ForeignKey("meetcampus_residents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("resident_b_id", sa.String(length=36), sa.ForeignKey("meetcampus_residents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("familiarity", sa.Integer(), nullable=False),
        sa.Column("trust", sa.Integer(), nullable=False),
        sa.Column("warmth", sa.Integer(), nullable=False),
        sa.Column("shared_interests", json_type, nullable=False),
        sa.Column("summary_zh", sa.Text(), nullable=False),
        sa.Column("summary_en", sa.Text(), nullable=False),
        sa.Column("last_event_id", sa.String(length=36), sa.ForeignKey("meetcampus_events.id", ondelete="SET NULL")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("resident_a_id", "resident_b_id", name="uq_meetcampus_relationship_pair"),
        sa.CheckConstraint("resident_a_id < resident_b_id", name="ck_meetcampus_relationship_order"),
    )
    op.create_table(
        "meetcampus_stories",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("owner_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("resident_id", sa.String(length=36), sa.ForeignKey("meetcampus_residents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title_zh", sa.String(length=180), nullable=False),
        sa.Column("title_en", sa.String(length=180), nullable=False),
        sa.Column("narration_zh", sa.Text(), nullable=False),
        sa.Column("narration_en", sa.Text(), nullable=False),
        sa.Column("event_ids", json_type, nullable=False),
        sa.Column("bridge_candidate", sa.Boolean(), nullable=False),
        sa.Column("is_viewed", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("viewed_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_meetcampus_stories_owner", "meetcampus_stories", ["owner_user_id", "created_at"])
    op.create_table(
        "meetcampus_bridges",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("source_event_id", sa.String(length=36), sa.ForeignKey("meetcampus_events.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("initiator_resident_id", sa.String(length=36), sa.ForeignKey("meetcampus_residents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("counterpart_resident_id", sa.String(length=36), sa.ForeignKey("meetcampus_residents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("initiator_owner_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("counterpart_owner_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("proposal", json_type, nullable=False),
        sa.Column("initiator_consented_at", sa.DateTime(timezone=True)),
        sa.Column("counterpart_consented_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "meetcampus_agent_runs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("resident_id", sa.String(length=36), sa.ForeignKey("meetcampus_residents.id", ondelete="SET NULL")),
        sa.Column("operation", sa.String(length=40), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("model", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("prompt_hash", sa.String(length=64), nullable=False),
        sa.Column("input_tokens", sa.Integer()),
        sa.Column("output_tokens", sa.Integer()),
        sa.Column("latency_ms", sa.Integer()),
        sa.Column("error_code", sa.String(length=80)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_meetcampus_agent_runs_time", "meetcampus_agent_runs", ["created_at"])

    now = datetime.now(timezone.utc)
    world = sa.table(
        "meetcampus_worlds",
        sa.column("id"), sa.column("slug"), sa.column("name_zh"), sa.column("name_en"),
        sa.column("status"), sa.column("seed_version"), sa.column("state_version"),
        sa.column("last_advanced_at"), sa.column("created_at"), sa.column("updated_at"),
    )
    op.bulk_insert(world, [{
        "id": WORLD_ID, "slug": "hkust-gz-campus", "name_zh": "科广大校园", "name_en": "HKUST(GZ) Campus",
        "status": "active", "seed_version": "private-beta-1", "state_version": 1,
        "last_advanced_at": now, "created_at": now, "updated_at": now,
    }])

    scene_table = sa.table(
        "meetcampus_scenes",
        sa.column("id"), sa.column("world_id"), sa.column("parent_scene_id"), sa.column("slug"),
        sa.column("kind"), sa.column("name_zh"), sa.column("name_en"), sa.column("map_x"), sa.column("map_y"),
        sa.column("affordances", json_type), sa.column("visual", json_type), sa.column("is_active"),
    )
    op.bulk_insert(scene_table, [{
        "id": sid, "world_id": WORLD_ID, "parent_scene_id": parent, "slug": slug, "kind": kind,
        "name_zh": zh, "name_en": en, "map_x": x, "map_y": y, "affordances": affordances,
        "visual": {"asset": f"{slug}.webp", "tone": kind}, "is_active": True,
    } for sid, parent, slug, kind, zh, en, x, y, affordances in SCENES])

    connection_table = sa.table(
        "meetcampus_scene_connections",
        sa.column("id"), sa.column("world_id"), sa.column("from_scene_id"), sa.column("to_scene_id"), sa.column("travel_minutes"),
    )
    macro_ids = [scene[0] for scene in SCENES[1:9]]
    connections = []
    for index, scene_id in enumerate(macro_ids):
        next_id = macro_ids[(index + 1) % len(macro_ids)]
        connections.extend([
            {"id": f"mc-conn-{index:02d}-a", "world_id": WORLD_ID, "from_scene_id": scene_id, "to_scene_id": next_id, "travel_minutes": 8 + index % 4},
            {"id": f"mc-conn-{index:02d}-b", "world_id": WORLD_ID, "from_scene_id": next_id, "to_scene_id": scene_id, "travel_minutes": 8 + index % 4},
        ])
    child_links = [
        ("mc-scene-gym", "mc-scene-gym-badminton"),
        ("mc-scene-library", "mc-scene-library-reading"),
        ("mc-scene-college", "mc-scene-college-lounge"),
    ]
    for index, (parent, child) in enumerate(child_links):
        connections.extend([
            {"id": f"mc-conn-child-{index}-a", "world_id": WORLD_ID, "from_scene_id": parent, "to_scene_id": child, "travel_minutes": 2},
            {"id": f"mc-conn-child-{index}-b", "world_id": WORLD_ID, "from_scene_id": child, "to_scene_id": parent, "travel_minutes": 2},
        ])
    op.bulk_insert(connection_table, connections)

    resident_table = sa.table(
        "meetcampus_residents",
        sa.column("id"), sa.column("world_id"), sa.column("slug"), sa.column("owner_user_id"),
        sa.column("is_synthetic"), sa.column("name_zh"), sa.column("name_en"), sa.column("pronouns", json_type),
        sa.column("persona", json_type), sa.column("appearance", json_type), sa.column("schedule", json_type),
        sa.column("voice", json_type), sa.column("is_active"), sa.column("created_at"), sa.column("updated_at"),
    )
    schedule_scene_slugs = ["library", "gym", "teaching-building", "college", "canteen", "student-center", "innovation-lab", "lakeside"]
    op.bulk_insert(resident_table, [{
        "id": rid, "world_id": WORLD_ID, "slug": slug, "owner_user_id": None, "is_synthetic": synthetic,
        "name_zh": zh, "name_en": en, "pronouns": {"zh": "它", "en": "they"},
        "persona": {"interests": interests, "temperament": temperament, "social_style": social_style, "values": ["curiosity", "respect", "follow_through"]},
        "appearance": {"palette": palette, "hair": index % 8, "outfit": index % 6, "accessory": index % 5},
        "schedule": [{"period": "morning", "scene": schedule_scene_slugs[index % 4]}, {"period": "afternoon", "scene": schedule_scene_slugs[(index + 3) % 8]}, {"period": "evening", "scene": schedule_scene_slugs[(index + 5) % 8]}],
        "voice": {"pace": social_style, "emoji": "rare", "verbosity": "brief"},
        "is_active": synthetic, "created_at": now, "updated_at": now,
    } for index, (rid, slug, synthetic, zh, en, interests, temperament, social_style, palette) in enumerate(RESIDENTS)])

    state_table = sa.table(
        "meetcampus_resident_states",
        sa.column("resident_id"), sa.column("scene_id"), sa.column("position_x"), sa.column("position_y"),
        sa.column("activity"), sa.column("activity_started_at"), sa.column("needs", json_type),
        sa.column("active_goal", json_type), sa.column("next_decision_at"), sa.column("last_decision_at"), sa.column("updated_at"),
    )
    initial_scene_ids = [scene[0] for scene in SCENES[1:9]]
    op.bulk_insert(state_table, [{
        "resident_id": resident[0], "scene_id": initial_scene_ids[index % len(initial_scene_ids)],
        "position_x": float(18 + (index * 17) % 67), "position_y": float(18 + (index * 23) % 67),
        "activity": "waiting_for_owner" if index == 0 else "following_routine",
        "activity_started_at": now, "needs": {"energy": 70 + index % 20, "social": 40 + (index * 7) % 45, "curiosity": 55 + (index * 11) % 40},
        "active_goal": {}, "next_decision_at": now + timedelta(minutes=35 + (index * 13) % 55),
        "last_decision_at": None, "updated_at": now,
    } for index, resident in enumerate(RESIDENTS)])


def downgrade():
    op.drop_index("ix_meetcampus_agent_runs_time", table_name="meetcampus_agent_runs")
    op.drop_table("meetcampus_agent_runs")
    op.drop_table("meetcampus_bridges")
    op.drop_index("ix_meetcampus_stories_owner", table_name="meetcampus_stories")
    op.drop_table("meetcampus_stories")
    op.drop_table("meetcampus_relationships")
    op.drop_index("ix_meetcampus_memories_resident", table_name="meetcampus_memories")
    op.drop_table("meetcampus_memories")
    op.drop_index("ix_meetcampus_events_resident_time", table_name="meetcampus_events")
    op.drop_table("meetcampus_events")
    op.drop_index("ix_meetcampus_commands_pending", table_name="meetcampus_commands")
    op.drop_table("meetcampus_commands")
    op.drop_table("meetcampus_owner_profiles")
    op.drop_index("ix_meetcampus_state_due", table_name="meetcampus_resident_states")
    op.drop_table("meetcampus_resident_states")
    op.drop_index("ix_meetcampus_residents_world", table_name="meetcampus_residents")
    op.drop_table("meetcampus_residents")
    op.drop_table("meetcampus_scene_connections")
    op.drop_table("meetcampus_scenes")
    op.drop_table("meetcampus_worlds")
