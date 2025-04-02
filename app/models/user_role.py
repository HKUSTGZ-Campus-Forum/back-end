from app.extensions import db

class UserRole(db.Model):
    __tablename__ = 'user_roles'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)
    description = db.Column(db.String(200))
    
    # Constants for easy access
    ADMIN = 'admin'
    MODERATOR = 'moderator'
    USER = 'user'
    
    @classmethod
    def get_role_id(cls, role_name):
        role = cls.query.filter_by(name=role_name).first()
        return role.id if role else None
