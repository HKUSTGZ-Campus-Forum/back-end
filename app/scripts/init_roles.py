from app import create_app
from app.extensions import db
from app.models.user_role import UserRole

def init_roles():
    """Initialize the user roles table"""
    app = create_app()
    with app.app_context():
        # Define the required roles
        required_roles = [
            (UserRole.ADMIN, "Administrator with full privileges"),
            (UserRole.MODERATOR, "Moderator with content management privileges"),
            (UserRole.USER, "Regular user with standard privileges")
        ]
        
        # Check each role and add if missing
        roles_added = 0
        for role_name, description in required_roles:
            if not UserRole.query.filter_by(name=role_name).first():
                role = UserRole(name=role_name, description=description)
                db.session.add(role)
                roles_added += 1
        
        if roles_added > 0:
            db.session.commit()
            print(f"{roles_added} user roles added successfully.")
        else:
            print("All required user roles already exist.")

if __name__ == "__main__":
    init_roles()
