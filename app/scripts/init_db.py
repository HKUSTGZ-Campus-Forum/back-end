from app import create_app
from app.extensions import db
from app.models.tag import TagType
from app.models.user_role import UserRole
from app.models.identity_type import IdentityType
from sqlalchemy import text

def init_tag_types():
    """Initialize predefined tag types if they don't exist"""
    app = create_app()
    with app.app_context():
        # Get or create tag types
        tag_types = [
            TagType.SYSTEM,
            TagType.USER,
            TagType.COURSE
        ]
        
        for type_name in tag_types:
            tag_type = TagType.query.filter_by(name=type_name).first()
            if not tag_type:
                print(f"Creating tag type: {type_name}")
                tag_type = TagType(name=type_name)
                db.session.add(tag_type)
        
        db.session.commit()
        print("Tag types initialization completed")

def init_user_roles():
    """Initialize predefined user roles if they don't exist"""
    app = create_app()
    with app.app_context():
        # Get or create user roles
        roles = [
            UserRole.ADMIN,
            UserRole.MODERATOR,
            UserRole.USER
        ]
        
        for role_name in roles:
            role = UserRole.query.filter_by(name=role_name).first()
            if not role:
                print(f"Creating user role: {role_name}")
                role = UserRole(name=role_name)
                db.session.add(role)
        
        db.session.commit()
        print("User roles initialization completed")

def init_identity_system():
    """Initialize identity verification system tables and data"""
    app = create_app()
    with app.app_context():
        try:
            # Check if identity_types table exists, if not create the whole system
            try:
                IdentityType.query.first()
                print("Identity system tables already exist")
            except Exception:
                print("Creating identity verification system tables...")
                
                # Create identity system tables with raw SQL to avoid migration conflicts
                db.session.execute(text("""
                    -- Create identity_types table
                    CREATE TABLE IF NOT EXISTS identity_types (
                        id SERIAL PRIMARY KEY,
                        name VARCHAR(50) UNIQUE NOT NULL,
                        display_name VARCHAR(100) NOT NULL,
                        color VARCHAR(7) DEFAULT '#2563eb' NOT NULL,
                        icon_name VARCHAR(50),
                        description TEXT,
                        is_active BOOLEAN DEFAULT true NOT NULL,
                        created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL
                    );

                    -- Create user_identities table  
                    CREATE TABLE IF NOT EXISTS user_identities (
                        id SERIAL PRIMARY KEY,
                        user_id INTEGER NOT NULL REFERENCES users(id),
                        identity_type_id INTEGER NOT NULL REFERENCES identity_types(id),
                        status VARCHAR(20) DEFAULT 'pending' NOT NULL,
                        verification_documents JSONB,
                        verified_by INTEGER REFERENCES users(id),
                        rejection_reason TEXT,
                        notes TEXT,
                        verified_at TIMESTAMPTZ,
                        expires_at TIMESTAMPTZ,
                        created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
                        updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
                        UNIQUE(user_id, identity_type_id),
                        CHECK (status IN ('pending', 'approved', 'rejected', 'revoked'))
                    );

                    -- Add display_identity_id columns to existing tables
                    ALTER TABLE posts ADD COLUMN IF NOT EXISTS display_identity_id INTEGER REFERENCES user_identities(id);
                    ALTER TABLE comments ADD COLUMN IF NOT EXISTS display_identity_id INTEGER REFERENCES user_identities(id);  
                    ALTER TABLE gugu_messages ADD COLUMN IF NOT EXISTS display_identity_id INTEGER REFERENCES user_identities(id);

                    -- Create indexes
                    CREATE INDEX IF NOT EXISTS idx_user_identities_status ON user_identities(status);
                    CREATE INDEX IF NOT EXISTS idx_user_identities_user_status ON user_identities(user_id, status);
                """))
                
                db.session.commit()
                print("✓ Identity system tables created successfully")
        
        except Exception as e:
            print(f"Error creating identity system tables: {e}")
            db.session.rollback()
            return
            
        # Define identity types with their display properties
        identity_types_data = [
            {
                'name': IdentityType.PROFESSOR,
                'display_name': 'Professor',
                'color': '#dc2626',
                'icon_name': 'academic-cap',
                'description': 'University professor or teaching staff'
            },
            {
                'name': IdentityType.STAFF,
                'display_name': 'Staff Member',
                'color': '#059669',
                'icon_name': 'user-group',
                'description': 'University administrative or support staff'
            },
            {
                'name': IdentityType.OFFICER,
                'display_name': 'School Officer',
                'color': '#7c3aed',
                'icon_name': 'shield-check',
                'description': 'Student government or official school organization officer'
            },
            {
                'name': IdentityType.STUDENT_LEADER,
                'display_name': 'Student Leader',
                'color': '#ea580c',
                'icon_name': 'star',
                'description': 'Student club president or community leader'
            }
        ]
        
        for identity_data in identity_types_data:
            identity_type = IdentityType.query.filter_by(name=identity_data['name']).first()
            if not identity_type:
                print(f"Creating identity type: {identity_data['name']}")
                identity_type = IdentityType(
                    name=identity_data['name'],
                    display_name=identity_data['display_name'],
                    color=identity_data['color'],
                    icon_name=identity_data['icon_name'],
                    description=identity_data['description']
                )
                db.session.add(identity_type)
            else:
                # Update existing identity type properties if needed
                identity_type.display_name = identity_data['display_name']
                identity_type.color = identity_data['color']
                identity_type.icon_name = identity_data['icon_name']
                identity_type.description = identity_data['description']
                print(f"Updated identity type: {identity_data['name']}")
        
        db.session.commit()
        print("Identity types initialization completed")

def init_db():
    """Initialize database with all required predefined data"""
    init_tag_types()
    init_user_roles()
    init_identity_system()  # This now handles both table creation and data population
    print("Database initialization completed")

if __name__ == '__main__':
    init_db() 