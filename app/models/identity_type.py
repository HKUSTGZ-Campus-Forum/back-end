from datetime import datetime, timezone
from app.extensions import db

class IdentityType(db.Model):
    __tablename__ = 'identity_types'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)  # 'professor', 'staff', 'officer'
    display_name = db.Column(db.String(100), nullable=False)  # 'Professor', 'Staff Member'
    color = db.Column(db.String(7), default='#2563eb', nullable=False)  # Hex color for display
    icon_name = db.Column(db.String(50))  # Icon identifier
    description = db.Column(db.Text)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    
    # Relationships
    user_identities = db.relationship('UserIdentity', backref='identity_type', lazy='dynamic', cascade='all, delete-orphan')
    
    # Constants for common identity types
    PROFESSOR = 'professor'
    STAFF = 'staff'
    OFFICER = 'officer'
    STUDENT_LEADER = 'student_leader'
    
    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "display_name": self.display_name,
            "color": self.color,
            "icon_name": self.icon_name,
            "description": self.description,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None
        }
    
    @classmethod
    def get_active_types(cls):
        """Get all active identity types"""
        return cls.query.filter_by(is_active=True).all()
    
    @classmethod
    def get_by_name(cls, name):
        """Get identity type by name"""
        return cls.query.filter_by(name=name, is_active=True).first()
    
    def __repr__(self):
        return f'<IdentityType {self.name}: {self.display_name}>'