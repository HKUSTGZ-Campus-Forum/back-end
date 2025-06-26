from app import create_app
from app.extensions import db
from app.models.tag import TagType, Tag
from app.models.user_role import UserRole
from app.models.post import Post
from app.models.user import User
from datetime import datetime, timezone

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

def init_instant_discussion():
    """Initialize instant discussion area"""
    app = create_app()
    with app.app_context():
        # Check if instant discussion post already exists
        existing_post = Post.query.join(Post.tags).filter(
            Tag.name == 'instant-discussion'
        ).first()
        
        if existing_post:
            print(f"Instant discussion post already exists with ID: {existing_post.id}")
            return existing_post.id
        
        # Get or create system tag type
        system_tag_type = TagType.query.filter_by(name=TagType.SYSTEM).first()
        if not system_tag_type:
            system_tag_type = TagType(name=TagType.SYSTEM)
            db.session.add(system_tag_type)
            db.session.flush()
        
        # Create instant-discussion tag
        instant_discussion_tag = Tag.query.filter_by(name='instant-discussion').first()
        if not instant_discussion_tag:
            instant_discussion_tag = Tag(
                name='instant-discussion',
                tag_type_id=system_tag_type.id,
                description='System tag for instant discussion area'
            )
            db.session.add(instant_discussion_tag)
            db.session.flush()
        
        # Get or create system user
        system_user = User.query.filter_by(username='system').first()
        if not system_user:
            # Create a system user if it doesn't exist
            system_user = User(
                username='system',
                email='system@campusforum.local',
                is_verified=True,
                created_at=datetime.now(timezone.utc)
            )
            system_user.set_password('system_password_not_for_login')
            db.session.add(system_user)
            db.session.flush()
        
        # Create the special post
        instant_discussion_post = Post(
            user_id=system_user.id,
            title='🚀 Instant Discussion Area',
            content='Welcome to the instant discussion area! This is a real-time chat space where all users can communicate instantly. Share your thoughts, ask questions, or just say hello! 💬✨',
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc)
        )
        
        db.session.add(instant_discussion_post)
        db.session.flush()
        
        # Add the instant-discussion tag to the post
        instant_discussion_post.tags.append(instant_discussion_tag)
        
        db.session.commit()
        
        print(f"Created instant discussion post with ID: {instant_discussion_post.id}")
        return instant_discussion_post.id

def init_db():
    """Initialize database with all required predefined data"""
    init_tag_types()
    init_user_roles()
    init_instant_discussion()
    print("Database initialization completed")

if __name__ == '__main__':
    init_db() 