from app import create_app
from app.extensions import db
from app.models.user_role import UserRole

def init_roles():
    """Initialize the user roles table"""
    app = create_app()
    with app.app_context():
        # Check if roles already exist
        if UserRole.query.count() == 0:
            roles = [
                UserRole(name=UserRole.ADMIN, description="Administrator with full privileges"),
                UserRole(name=UserRole.MODERATOR, description="Moderator with content management privileges"),
                UserRole(name=UserRole.USER, description="Regular user with standard privileges")
            ]
            db.session.add_all(roles)
            db.session.commit()
            print("User roles initialized successfully.")
        else:
            print("User roles already exist.")

if __name__ == "__main__":
    init_roles()
