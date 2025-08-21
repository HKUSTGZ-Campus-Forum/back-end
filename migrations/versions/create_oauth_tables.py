"""Create OAuth tables

Revision ID: create_oauth_tables
Revises: 
Create Date: 2025-01-21

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'create_oauth_tables'
down_revision = None  # Update this to your latest migration
depends_on = None

def upgrade():
    # Create oauth_clients table
    op.create_table('oauth_clients',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('client_id', sa.String(length=40), nullable=False),
        sa.Column('client_secret', sa.String(length=55), nullable=False),
        sa.Column('client_name', sa.String(length=100), nullable=False),
        sa.Column('client_description', sa.Text(), nullable=True),
        sa.Column('client_uri', sa.String(length=255), nullable=True),
        sa.Column('redirect_uris', sa.Text(), nullable=True),
        sa.Column('scope', sa.Text(), nullable=True),
        sa.Column('response_types', sa.Text(), nullable=True),
        sa.Column('grant_types', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_oauth_clients_client_id', 'oauth_clients', ['client_id'], unique=True)
    
    # Create oauth_authorization_codes table
    op.create_table('oauth_authorization_codes',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('code', sa.String(length=255), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('client_id', sa.String(length=40), nullable=False),
        sa.Column('redirect_uri', sa.String(length=255), nullable=False),
        sa.Column('scope', sa.Text(), nullable=True),
        sa.Column('code_challenge', sa.String(length=128), nullable=True),
        sa.Column('code_challenge_method', sa.String(length=10), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('used', sa.Boolean(), nullable=False),
        sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['client_id'], ['oauth_clients.client_id'], ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_oauth_authorization_codes_code', 'oauth_authorization_codes', ['code'], unique=True)
    
    # Create oauth_tokens table
    op.create_table('oauth_tokens',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('access_token', sa.String(length=255), nullable=False),
        sa.Column('refresh_token', sa.String(length=255), nullable=True),
        sa.Column('token_type', sa.String(length=40), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('client_id', sa.String(length=40), nullable=False),
        sa.Column('scope', sa.Text(), nullable=True),
        sa.Column('expires_in', sa.Integer(), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('revoked', sa.Boolean(), nullable=False),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['client_id'], ['oauth_clients.client_id'], ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_oauth_tokens_access_token', 'oauth_tokens', ['access_token'], unique=True)
    op.create_index('ix_oauth_tokens_refresh_token', 'oauth_tokens', ['refresh_token'], unique=True)

def downgrade():
    op.drop_index('ix_oauth_tokens_refresh_token', table_name='oauth_tokens')
    op.drop_index('ix_oauth_tokens_access_token', table_name='oauth_tokens')
    op.drop_table('oauth_tokens')
    
    op.drop_index('ix_oauth_authorization_codes_code', table_name='oauth_authorization_codes')
    op.drop_table('oauth_authorization_codes')
    
    op.drop_index('ix_oauth_clients_client_id', table_name='oauth_clients')
    op.drop_table('oauth_clients')