"""empty message

Revision ID: b79b55da2342
Revises: 71258dd20b96
Create Date: 2026-04-16 12:51:36.912018

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b79b55da2342'
down_revision = '71258dd20b96'
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    columns = {
        column["name"]
        for column in inspector.get_columns("contest_submissions")
    }
    if "track" not in columns:
        op.add_column(
            "contest_submissions",
            sa.Column(
                "track",
                sa.String(length=20),
                nullable=False,
                server_default="tech",
            ),
        )

    inspector = sa.inspect(op.get_bind())
    matching_constraints = {
        constraint["name"]
        for constraint in inspector.get_unique_constraints("contest_submissions")
        if constraint.get("name") == "uq_contest_submissions_user_track"
        or constraint.get("column_names") == ["user_id", "track"]
    }
    matching_indexes = {
        index["name"]
        for index in inspector.get_indexes("contest_submissions")
        if index.get("name") == "uq_contest_submissions_user_track"
        or (
            index.get("unique")
            and index.get("column_names") == ["user_id", "track"]
        )
    }

    if not matching_constraints:
        with op.batch_alter_table('contest_submissions', schema=None) as batch_op:
            for index_name in matching_indexes:
                batch_op.drop_index(index_name)
            batch_op.create_unique_constraint(
                'uq_contest_submissions_user_track',
                ['user_id', 'track'],
            )


def downgrade():
    inspector = sa.inspect(op.get_bind())
    matching_constraints = {
        constraint["name"]
        for constraint in inspector.get_unique_constraints("contest_submissions")
        if constraint.get("column_names") == ["user_id", "track"]
    }
    with op.batch_alter_table('contest_submissions', schema=None) as batch_op:
        for constraint_name in matching_constraints:
            batch_op.drop_constraint(constraint_name, type_='unique')

    columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("contest_submissions")
    }
    if "track" in columns:
        op.drop_column("contest_submissions", "track")
