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
    email = db.Column(db.String(100), nullable=True)
    email_verified = db.Column(db.Boolean, default=False, nullable=False)
    email_verification_code = db.Column(db.String(6), nullable=True)
    email_verification_expires_at = db.Column(db.DateTime(timezone=True), nullable=True)
    password_reset_token = db.Column(db.String(64), nullable=True)
    password_reset_expires_at = db.Column(db.DateTime(timezone=True), nullable=True)
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

    def set_email_verification_code(self, code, expires_minutes=10):
        """Set email verification code with expiration"""
        from datetime import timedelta
        self.email_verification_code = code
        self.email_verification_expires_at = datetime.now(timezone.utc) + timedelta(minutes=expires_minutes)

    def verify_email_code(self, code):
        """Verify email verification code"""
        if not self.email_verification_code or not self.email_verification_expires_at:
            return False
        
        if datetime.now(timezone.utc) > self.email_verification_expires_at:
            return False
        
        if self.email_verification_code == code:
            self.email_verified = True
            self.email_verification_code = None
            self.email_verification_expires_at = None
            return True
        
        return False

    def set_password_reset_token(self, token, expires_hours=1):
        """Set password reset token with expiration"""
        from datetime import timedelta
        self.password_reset_token = token
        self.password_reset_expires_at = datetime.now(timezone.utc) + timedelta(hours=expires_hours)

    def verify_password_reset_token(self, token):
        """Verify password reset token"""
        if not self.password_reset_token or not self.password_reset_expires_at:
            return False
        
        if datetime.now(timezone.utc) > self.password_reset_expires_at:
            return False
        
        return self.password_reset_token == token

    def clear_password_reset_token(self):
        """Clear password reset token after use"""
        self.password_reset_token = None
        self.password_reset_expires_at = None

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
        avatar_url = self.avatar_url  # Generate fresh signed URL once
        data = {
            "id": self.id,
            "username": self.username,
            "profile_picture_url": avatar_url,  # Legacy field for backward compatibility
            "avatar_url": avatar_url,  # New field for fresh signed URLs
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
        
        # Include user identities (approved identities are public, all identities for own profile)
        try:
            from app.models.user_identity import UserIdentity
            if include_contact:
                # Own profile: include all identities
                identities = UserIdentity.query.filter_by(user_id=self.id).all()
            else:
                # Other's profile: only show approved identities
                identities = UserIdentity.query.filter_by(
                    user_id=self.id, 
                    status=UserIdentity.APPROVED
                ).all()
            
            data["identities"] = [identity.to_dict() for identity in identities]
        except ImportError:
            # In case the identity system isn't available
            data["identities"] = []
            
        return data
