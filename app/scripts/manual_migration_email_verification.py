"""Add email verification fields

Revision ID: email_verification_001
Revises: 
Create Date: 2025-08-16 16:50:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers
revision = 'email_verification_001'
down_revision = None  # This will work regardless of previous migrations
branch_labels = None
depends_on = None


def upgrade():
    """Add email verification fields to users table"""
    
    # Check if columns already exist before adding them
    conn = op.get_bind()
    
    # Add email_verification_code if it doesn't exist
    try:
        op.add_column('users', sa.Column('email_verification_code', sa.String(length=6), nullable=True))
    except Exception:
        pass  # Column already exists
    
    # Add email_verification_expires_at if it doesn't exist
    try:
        op.add_column('users', sa.Column('email_verification_expires_at', sa.DateTime(timezone=True), nullable=True))
    except Exception:
        pass  # Column already exists
    
    # Add password_reset_token if it doesn't exist
    try:
        op.add_column('users', sa.Column('password_reset_token', sa.String(length=64), nullable=True))
    except Exception:
        pass  # Column already exists
    
    # Add password_reset_expires_at if it doesn't exist
    try:
        op.add_column('users', sa.Column('password_reset_expires_at', sa.DateTime(timezone=True), nullable=True))
    except Exception:
        pass  # Column already exists
    
    # Create indexes for performance (if they don't exist)
    try:
        op.create_index('idx_users_email_verification_code', 'users', ['email_verification_code'])
    except Exception:
        pass  # Index already exists
        
    try:
        op.create_index('idx_users_password_reset_token', 'users', ['password_reset_token'])
    except Exception:
        pass  # Index already exists


def downgrade():
    """Remove email verification fields"""
    
    # Remove indexes
    try:
        op.drop_index('idx_users_password_reset_token', table_name='users')
    except Exception:
        pass
        
    try:
        op.drop_index('idx_users_email_verification_code', table_name='users')
    except Exception:
        pass
    
    # Remove columns
    try:
        op.drop_column('users', 'password_reset_expires_at')
    except Exception:
        pass
        
    try:
        op.drop_column('users', 'password_reset_token')
    except Exception:
        pass
        
    try:
        op.drop_column('users', 'email_verification_expires_at')
    except Exception:
        pass
        
    try:
        op.drop_column('users', 'email_verification_code')
    except Exception:
        pass