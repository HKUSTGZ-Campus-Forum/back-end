# app/models/user.py
from datetime import datetime, timezone
from app.extensions import db
from werkzeug.security import generate_password_hash, check_password_hash
from app.models.user_role import UserRole as UserRoleModel

class User(db.Model):
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), nullable=False)
    password_hash = db.Column(db.Text, nullable=False)
    email = db.Column(db.String(100))
    email_verified = db.Column(db.Boolean, default=False, nullable=False)
    phone_number = db.Column(db.String(20))
    phone_verified = db.Column(db.Boolean, default=False, nullable=False)
    profile_picture_file_id = db.Column(db.Integer, db.ForeignKey('files.id'), nullable=True)  # Reference to File record for avatar
    role_id = db.Column(db.Integer, db.ForeignKey('user_roles.id'), nullable=False)
    is_deleted = db.Column(db.Boolean, default=False, nullable=False)
    deleted_at = db.Column(db.DateTime(timezone=True))
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
                          onupdate=lambda: datetime.now(timezone.utc), nullable=False)
    last_active_at = db.Column(db.DateTime(timezone=True), nullable=True)

    # Relationships
    role = db.relationship('UserRole', backref=db.backref('users', lazy='dynamic'))
    posts = db.relationship('Post', backref='author', lazy='dynamic', cascade='all, delete-orphan')
    comments = db.relationship('Comment', backref='author', lazy='dynamic', cascade='all, delete-orphan')
    reactions = db.relationship('Reaction', backref='user', lazy='dynamic', cascade='all, delete-orphan')
    calendar_entries = db.relationship('UserCalendar', backref='user', lazy='dynamic', cascade='all, delete-orphan')
    files = db.relationship('File', foreign_keys='File.user_id', backref='owner', lazy='dynamic', cascade='all, delete-orphan')
    profile_picture_file = db.relationship('File', foreign_keys=[profile_picture_file_id], post_update=True, uselist=False) 

    # Add constraint for unique username among active users
    __table_args__ = (
        db.UniqueConstraint(
            'username', 
            name='uq_users_username_active', 
            # postgresql_where=(db.text("is_deleted IS FALSE"))
        ),
    )

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)
    
    def get_role_name(self):
        """Get the role name from the role relationship"""
        return self.role.name if self.role else None
        
    def is_admin(self):
        """Check if the user has admin role"""
        role_name = self.get_role_name()
        return role_name == UserRoleModel.ADMIN
        
    def is_moderator(self):
        """Check if the user has moderator role"""
        role_name = self.get_role_name()
        return role_name == UserRoleModel.MODERATOR

    def get_effective_last_active(self):
        """Get the effective last active time, falling back to created_at if last_active_at is None"""
        return self.last_active_at or self.created_at

    def update_last_active(self):
        """Update the last_active_at timestamp to current time"""
        self.last_active_at = datetime.now(timezone.utc)
        db.session.commit()

    @property
    def avatar_url(self):
        """Generate a fresh signed URL for the user's avatar"""
        if self.profile_picture_file_id and self.profile_picture_file:
            try:
                return self.profile_picture_file.url  # This generates a fresh signed URL each time
            except Exception as e:
                # Log error but don't crash
                from flask import current_app
                if current_app:
                    current_app.logger.error(f"Error generating avatar URL for user {self.id}: {e}")
        
        # No avatar available
        return None

    def to_dict(self, include_contact=False, include_last_active=False):
        data = {
            "id": self.id,
            "username": self.username,
            "profile_picture_url": self.avatar_url,  # Always generate fresh signed URL
            "role_id": self.role_id,
            "role_name": self.get_role_name(),
            "is_deleted": self.is_deleted,
            "created_at": self.created_at.isoformat() if self.created_at else None
        }
        
        if include_contact:
            data["email"] = self.email
            data["email_verified"] = self.email_verified
            data["phone_number"] = self.phone_number
            data["phone_verified"] = self.phone_verified
            
        # Only include last_active_at if specifically requested
        if include_last_active:
            data["last_active_at"] = self.last_active_at.isoformat() if self.last_active_at else None
            
        return data
