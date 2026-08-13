"""merge oauth into main

Revision ID: 3fc3cff37648
Revises: 9e590f09c480, create_oauth_tables
Create Date: 2025-08-21 21:13:53.839464

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '3fc3cff37648'
down_revision = ('9e590f09c480', 'create_oauth_tables')
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
