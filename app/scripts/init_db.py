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

def init_identity_types():
    """Initialize predefined identity types if they don't exist"""
    app = create_app()
    with app.app_context():
        # Check if identity_types table exists (flask db migrate should have created it)
        try:
            # Try to query the table - if it fails, flask db migrate didn't work
            IdentityType.query.first()
        except Exception as e:
            print(f"Identity types table not found - flask db migrate may have failed: {e}")
            print("Please check migration logs and try manual deployment")
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
    init_identity_types()  # Back to just populating data, not creating tables
    print("Database initialization completed")

if __name__ == '__main__':
    init_db() 