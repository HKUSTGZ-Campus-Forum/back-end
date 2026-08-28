"""Add managed bilingual home carousel.

Revision ID: 20260828_home_carousel
Revises: 20260828_meetcampus_world
"""

from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa


revision = "20260828_home_carousel"
down_revision = "20260828_meetcampus_world"
branch_labels = None
depends_on = None
expected_seed_counts = {"public.home_carousel_slides": 3}


def upgrade():
    op.create_table(
        "home_carousel_slides",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("locale", sa.String(length=8), nullable=False),
        sa.Column(
            "image_file_id",
            sa.Integer(),
            sa.ForeignKey("files.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("image_path", sa.String(length=512), nullable=True),
        sa.Column("alt_text_zh", sa.String(length=255), nullable=True),
        sa.Column("alt_text_en", sa.String(length=255), nullable=True),
        sa.Column("href", sa.String(length=2048), nullable=True),
        sa.Column("presentation_variant", sa.String(length=40), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), nullable=False),
        sa.Column(
            "created_by_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "updated_by_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "deleted_by_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("locale IN ('zh', 'en', 'all')", name="ck_home_carousel_locale"),
        sa.CheckConstraint(
            "presentation_variant IN ('image', 'scheduler')",
            name="ck_home_carousel_variant",
        ),
        sa.CheckConstraint(
            "NOT (image_file_id IS NOT NULL AND image_path IS NOT NULL)",
            name="ck_home_carousel_image_source",
        ),
    )
    op.create_index("ix_home_carousel_slides_locale", "home_carousel_slides", ["locale"])
    op.create_index("ix_home_carousel_slides_image_file_id", "home_carousel_slides", ["image_file_id"])
    op.create_index("ix_home_carousel_slides_sort_order", "home_carousel_slides", ["sort_order"])
    op.create_index("ix_home_carousel_slides_is_active", "home_carousel_slides", ["is_active"])
    op.create_index("ix_home_carousel_slides_is_deleted", "home_carousel_slides", ["is_deleted"])
    op.create_index(
        "idx_home_carousel_public",
        "home_carousel_slides",
        ["is_deleted", "is_active", "locale", "sort_order"],
    )

    now = datetime.now(timezone.utc)
    slides = sa.table(
        "home_carousel_slides",
        sa.column("locale"),
        sa.column("image_path"),
        sa.column("alt_text_zh"),
        sa.column("alt_text_en"),
        sa.column("href"),
        sa.column("presentation_variant"),
        sa.column("sort_order"),
        sa.column("is_active"),
        sa.column("is_deleted"),
        sa.column("created_at"),
        sa.column("updated_at"),
    )
    op.bulk_insert(slides, [
        {
            "locale": "all",
            "image_path": "/image/banner/scheduler-planner-hero.webp",
            "alt_text_zh": "UniKorn 排课助手横幅",
            "alt_text_en": "UniKorn course planner banner",
            "href": "/courses/planner",
            "presentation_variant": "scheduler",
            "sort_order": 10,
            "is_active": True,
            "is_deleted": False,
            "created_at": now,
            "updated_at": now,
        },
        {
            "locale": "zh",
            "image_path": "/image/banner/welcome_cn_2.jpg",
            "alt_text_zh": "UniKorn 中文欢迎横幅",
            "alt_text_en": None,
            "href": "/",
            "presentation_variant": "image",
            "sort_order": 20,
            "is_active": True,
            "is_deleted": False,
            "created_at": now,
            "updated_at": now,
        },
        {
            "locale": "en",
            "image_path": "/image/banner/welcome_en.jpg",
            "alt_text_zh": None,
            "alt_text_en": "UniKorn welcome banner in English",
            "href": "/",
            "presentation_variant": "image",
            "sort_order": 30,
            "is_active": True,
            "is_deleted": False,
            "created_at": now,
            "updated_at": now,
        },
    ])


def downgrade():
    op.drop_index("idx_home_carousel_public", table_name="home_carousel_slides")
    op.drop_index("ix_home_carousel_slides_is_deleted", table_name="home_carousel_slides")
    op.drop_index("ix_home_carousel_slides_is_active", table_name="home_carousel_slides")
    op.drop_index("ix_home_carousel_slides_sort_order", table_name="home_carousel_slides")
    op.drop_index("ix_home_carousel_slides_image_file_id", table_name="home_carousel_slides")
    op.drop_index("ix_home_carousel_slides_locale", table_name="home_carousel_slides")
    op.drop_table("home_carousel_slides")
