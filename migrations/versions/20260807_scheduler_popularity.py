"""add anonymous scheduler popularity support

Revision ID: 20260807_sched_popularity
Revises: 20260610_gugu_soft_delete
Create Date: 2026-08-07
"""

from alembic import op
import sqlalchemy as sa


revision = "20260807_sched_popularity"
down_revision = "20260610_gugu_soft_delete"
branch_labels = None
depends_on = None


def _index_names(inspector, table_name):
    if table_name not in inspector.get_table_names():
        return set()
    return {index["name"] for index in inspector.get_indexes(table_name)}


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if "scheduler_popularity_events" not in inspector.get_table_names():
        op.create_table(
            "scheduler_popularity_events",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("offering_id", sa.Integer(), nullable=False),
            sa.Column("section_id", sa.Integer(), nullable=True),
            sa.Column("section_source_id", sa.String(length=32), nullable=True),
            sa.Column("from_state", sa.String(length=16), nullable=True),
            sa.Column("to_state", sa.String(length=16), nullable=True),
            sa.Column("reason", sa.String(length=32), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.CheckConstraint(
                "from_state IS NULL OR from_state IN ('looking', 'scheduling')",
                name="valid_scheduler_popularity_from_state",
            ),
            sa.CheckConstraint(
                "to_state IS NULL OR to_state IN ('looking', 'scheduling')",
                name="valid_scheduler_popularity_to_state",
            ),
            sa.CheckConstraint(
                "(from_state IS NOT NULL OR to_state IS NOT NULL) "
                "AND (from_state IS NULL OR to_state IS NULL OR from_state <> to_state)",
                name="valid_scheduler_popularity_transition",
            ),
            sa.CheckConstraint(
                "reason IN ('cart_added', 'cart_removed', 'course_toggled', "
                "'bundle_toggled', 'layer_toggled')",
                name="valid_scheduler_popularity_reason",
            ),
            sa.ForeignKeyConstraint(
                ["offering_id"],
                ["course_offerings.id"],
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["section_id"],
                ["course_sections.id"],
                ondelete="SET NULL",
            ),
            sa.PrimaryKeyConstraint("id"),
        )

    inspector = sa.inspect(op.get_bind())
    event_indexes = _index_names(inspector, "scheduler_popularity_events")
    if "idx_scheduler_popularity_offering_created" not in event_indexes:
        op.create_index(
            "idx_scheduler_popularity_offering_created",
            "scheduler_popularity_events",
            ["offering_id", "created_at"],
        )
    if "idx_scheduler_popularity_section_created" not in event_indexes:
        op.create_index(
            "idx_scheduler_popularity_section_created",
            "scheduler_popularity_events",
            ["section_id", "created_at"],
        )
    if "idx_scheduler_popularity_source_created" not in event_indexes:
        op.create_index(
            "idx_scheduler_popularity_source_created",
            "scheduler_popularity_events",
            ["offering_id", "section_source_id", "created_at"],
        )

    inspector = sa.inspect(op.get_bind())
    cart_indexes = _index_names(inspector, "user_offering_carts")
    if (
        "user_offering_carts" in inspector.get_table_names()
        and "idx_user_offering_carts_popularity" not in cart_indexes
    ):
        op.create_index(
            "idx_user_offering_carts_popularity",
            "user_offering_carts",
            ["offering_id", "enabled", "user_id"],
        )

    selection_indexes = _index_names(inspector, "user_section_selections")
    if (
        "user_section_selections" in inspector.get_table_names()
        and "idx_user_section_selections_popularity" not in selection_indexes
    ):
        op.create_index(
            "idx_user_section_selections_popularity",
            "user_section_selections",
            ["offering_id", "enabled", "section_id", "user_id"],
        )

    # Older destructive timetable imports could leave an existing cart with no
    # section rows. The API historically interpreted those missing rows as
    # enabled, so materialize that same state before popularity starts counting.
    inspector = sa.inspect(op.get_bind())
    required_tables = {
        "course_sections",
        "user_offering_carts",
        "user_section_selections",
    }
    if required_tables.issubset(set(inspector.get_table_names())):
        op.execute(sa.text("""
            INSERT INTO user_section_selections (
                user_id,
                offering_id,
                section_id,
                enabled,
                source,
                created_at,
                updated_at
            )
            SELECT
                carts.user_id,
                sections.offering_id,
                sections.id,
                COALESCE((
                    SELECT inherited.enabled
                    FROM user_section_selections AS inherited
                    JOIN course_sections AS inherited_section
                      ON inherited_section.id = inherited.section_id
                     AND inherited_section.offering_id = inherited.offering_id
                    WHERE inherited.user_id = carts.user_id
                      AND inherited.offering_id = sections.offering_id
                      AND inherited_section.bundle = sections.bundle
                      AND inherited_section.layer = sections.layer
                    ORDER BY inherited_section.source_section_id
                    LIMIT 1
                ), TRUE),
                'cart',
                CURRENT_TIMESTAMP,
                CURRENT_TIMESTAMP
            FROM user_offering_carts AS carts
            JOIN course_sections AS sections
              ON sections.offering_id = carts.offering_id
            WHERE NOT EXISTS (
                SELECT 1
                FROM user_section_selections AS existing
                WHERE existing.user_id = carts.user_id
                  AND existing.offering_id = carts.offering_id
                  AND existing.section_id = sections.id
            )
        """))


def downgrade():
    inspector = sa.inspect(op.get_bind())
    selection_indexes = _index_names(inspector, "user_section_selections")
    if "idx_user_section_selections_popularity" in selection_indexes:
        op.drop_index(
            "idx_user_section_selections_popularity",
            table_name="user_section_selections",
        )

    cart_indexes = _index_names(inspector, "user_offering_carts")
    if "idx_user_offering_carts_popularity" in cart_indexes:
        op.drop_index(
            "idx_user_offering_carts_popularity",
            table_name="user_offering_carts",
        )

    if "scheduler_popularity_events" in inspector.get_table_names():
        op.drop_table("scheduler_popularity_events")
