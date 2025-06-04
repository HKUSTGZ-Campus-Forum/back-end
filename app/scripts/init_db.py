from app import create_app
from app.extensions import db
from app.models.tag import TagType
from app.models.user_role import UserRole

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

def init_db():
    """Initialize database with all required predefined data"""
    init_tag_types()
    init_user_roles()
    print("Database initialization completed")

if __name__ == '__main__':
    init_db() 