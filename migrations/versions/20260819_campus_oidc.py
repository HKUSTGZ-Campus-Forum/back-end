"""add campus OIDC identity mappings and one-time login tickets

Revision ID: 20260819_campus_oidc
Revises: 20260813_feedback_schema, 20260813_pop_history_truth
Create Date: 2026-08-19
"""

from alembic import op
import sqlalchemy as sa


revision = "20260819_campus_oidc"
down_revision = ("20260813_feedback_schema", "20260813_pop_history_truth")
branch_labels = None
depends_on = None


def upgrade():
    existing = set(sa.inspect(op.get_bind()).get_table_names())

    if "user_oidc_identities" not in existing:
        op.create_table(
            "user_oidc_identities",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("issuer", sa.String(length=255), nullable=False),
            sa.Column("subject", sa.String(length=255), nullable=False),
            sa.Column("last_seen_email", sa.String(length=100), nullable=True),
            sa.Column("display_name", sa.String(length=200), nullable=True),
            sa.Column("account_type", sa.String(length=50), nullable=True),
            sa.Column("department", sa.String(length=100), nullable=True),
            sa.Column("employee_id", sa.String(length=100), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(
                ["user_id"],
                ["users.id"],
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "issuer",
                "subject",
                name="uq_user_oidc_identities_issuer_subject",
            ),
        )
        op.create_index(
            "ix_user_oidc_identities_user_id",
            "user_oidc_identities",
            ["user_id"],
            unique=False,
        )

    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if "oidc_login_tickets" not in existing:
        op.create_table(
            "oidc_login_tickets",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("code_hash", sa.String(length=64), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("return_to", sa.String(length=512), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(
                ["user_id"],
                ["users.id"],
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("code_hash"),
        )
        op.create_index(
            "ix_oidc_login_tickets_user_id",
            "oidc_login_tickets",
            ["user_id"],
            unique=False,
        )
        op.create_index(
            "ix_oidc_login_tickets_expires_at",
            "oidc_login_tickets",
            ["expires_at"],
            unique=False,
        )


def downgrade():
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if "oidc_login_tickets" in existing:
        op.drop_index(
            "ix_oidc_login_tickets_expires_at",
            table_name="oidc_login_tickets",
        )
        op.drop_index(
            "ix_oidc_login_tickets_user_id",
            table_name="oidc_login_tickets",
        )
        op.drop_table("oidc_login_tickets")

    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if "user_oidc_identities" in existing:
        op.drop_index(
            "ix_user_oidc_identities_user_id",
            table_name="user_oidc_identities",
        )
        op.drop_table("user_oidc_identities")
