"""Add identity verification system

Revision ID: add_identity_verification_system
Revises: 
Create Date: 2025-08-28

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'add_identity_verification_system'
down_revision = None
depends_on = None

def upgrade():
    # Check if identity_types table already exists before creating
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    
    # Only create tables if they don't exist
    if not inspector.has_table('identity_types'):
        print("Creating identity_types table...")
        op.create_table('identity_types',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('name', sa.String(length=50), nullable=False),
            sa.Column('display_name', sa.String(length=100), nullable=False),
            sa.Column('color', sa.String(length=7), nullable=False, server_default='#2563eb'),
            sa.Column('icon_name', sa.String(length=50), nullable=True),
            sa.Column('description', sa.Text(), nullable=True),
            sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('name', name='uq_identity_types_name')
        )
        
        # Insert default identity types
        op.execute("""
            INSERT INTO identity_types (name, display_name, color, icon_name, description, created_at)
            VALUES 
            ('professor', 'Professor', '#dc2626', 'academic-cap', 'University professor or teaching staff', NOW()),
            ('staff', 'Staff Member', '#059669', 'user-group', 'University administrative or support staff', NOW()),
            ('officer', 'School Officer', '#7c3aed', 'shield-check', 'Student government or official school organization officer', NOW()),
            ('student_leader', 'Student Leader', '#ea580c', 'star', 'Student club president or community leader', NOW())
        """)
        print("Identity types created and populated.")
    else:
        print("Identity types table already exists, skipping creation.")
    
    if not inspector.has_table('user_identities'):
        print("Creating user_identities table...")
        op.create_table('user_identities',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('user_id', sa.Integer(), nullable=False),
            sa.Column('identity_type_id', sa.Integer(), nullable=False),
            sa.Column('status', sa.String(length=20), nullable=False, server_default='pending'),
            sa.Column('verification_documents', postgresql.JSONB(), nullable=True),
            sa.Column('verified_by', sa.Integer(), nullable=True),
            sa.Column('rejection_reason', sa.Text(), nullable=True),
            sa.Column('notes', sa.Text(), nullable=True),
            sa.Column('verified_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
            sa.CheckConstraint("status IN ('pending', 'approved', 'rejected', 'revoked')", name='ck_user_identities_status'),
            sa.ForeignKeyConstraint(['identity_type_id'], ['identity_types.id'], ),
            sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
            sa.ForeignKeyConstraint(['verified_by'], ['users.id'], ),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('user_id', 'identity_type_id', name='uq_user_identity_type')
        )
        
        # Create indexes for user_identities
        op.create_index('idx_user_identities_status', 'user_identities', ['status'])
        op.create_index('idx_user_identities_user_status', 'user_identities', ['user_id', 'status'])
        print("User identities table created.")
    else:
        print("User identities table already exists, skipping creation.")
    
    # Add display_identity_id columns to existing tables (with existence checks)
    if inspector.has_table('posts') and not inspector.has_column('posts', 'display_identity_id'):
        print("Adding display_identity_id to posts table...")
        op.add_column('posts', sa.Column('display_identity_id', sa.Integer(), nullable=True))
        
    if inspector.has_table('comments') and not inspector.has_column('comments', 'display_identity_id'):
        print("Adding display_identity_id to comments table...")
        op.add_column('comments', sa.Column('display_identity_id', sa.Integer(), nullable=True))
        
    if inspector.has_table('gugu_messages') and not inspector.has_column('gugu_messages', 'display_identity_id'):
        print("Adding display_identity_id to gugu_messages table...")
        op.add_column('gugu_messages', sa.Column('display_identity_id', sa.Integer(), nullable=True))
    
    # Add foreign key constraints for display_identity_id (only if both tables exist)
    if inspector.has_table('user_identities'):
        if inspector.has_table('posts'):
            try:
                op.create_foreign_key('fk_posts_display_identity', 'posts', 'user_identities', ['display_identity_id'], ['id'])
                print("Added foreign key constraint for posts.display_identity_id")
            except Exception as e:
                print(f"Foreign key constraint for posts already exists or failed: {e}")
                
        if inspector.has_table('comments'):
            try:
                op.create_foreign_key('fk_comments_display_identity', 'comments', 'user_identities', ['display_identity_id'], ['id'])
                print("Added foreign key constraint for comments.display_identity_id")
            except Exception as e:
                print(f"Foreign key constraint for comments already exists or failed: {e}")
                
        if inspector.has_table('gugu_messages'):
            try:
                op.create_foreign_key('fk_gugu_messages_display_identity', 'gugu_messages', 'user_identities', ['display_identity_id'], ['id'])
                print("Added foreign key constraint for gugu_messages.display_identity_id")
            except Exception as e:
                print(f"Foreign key constraint for gugu_messages already exists or failed: {e}")
    
    print("Identity verification system migration completed successfully!")

def downgrade():
    # This is a complex downgrade, implement if needed
    print("Downgrade not implemented for identity verification system")
    pass