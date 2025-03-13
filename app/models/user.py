# app/models/user.py
from datetime import datetime, timezone
from app.extensions import db
from werkzeug.security import generate_password_hash, check_password_hash

class UserRole:
    ADMIN = 'admin'
    MODERATOR = 'moderator'
    USER = 'user'

class User(db.Model):
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.Text, nullable=False)
    email = db.Column(db.String(100))
    email_verified = db.Column(db.Boolean, default=False)
    phone_number = db.Column(db.String(20))
    phone_verified = db.Column(db.Boolean, default=False)
    profile_picture_url = db.Column(db.Text)
    role = db.Column(db.String(20), default=UserRole.USER)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))

    # Relationships
    posts = db.relationship('Post', backref='author', lazy='dynamic', cascade='all, delete-orphan')
    comments = db.relationship('Comment', backref='author', lazy='dynamic', cascade='all, delete-orphan')
    reactions = db.relationship('Reaction', backref='user', lazy='dynamic', cascade='all, delete-orphan')
    calendar_entries = db.relationship('UserCalendar', backref='user', lazy='dynamic', cascade='all, delete-orphan')

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def to_dict(self, include_contact=False):
        data = {
            "id": self.id,
            "username": self.username,
            "profile_picture_url": self.profile_picture_url,
            "role": self.role,
            "created_at": self.created_at.isoformat()
        }
        
        if include_contact:
            data["email"] = self.email
            data["email_verified"] = self.email_verified
            data["phone_number"] = self.phone_number
            data["phone_verified"] = self.phone_verified
            
        return data
