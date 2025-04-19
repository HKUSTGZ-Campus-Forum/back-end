from datetime import datetime, timezone
from app.extensions import db

class File(db.Model):
    __tablename__ = 'files'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    object_name = db.Column(db.String(255), nullable=False, unique=True)
    original_filename = db.Column(db.String(255), nullable=False)
    file_size = db.Column(db.Integer)  # Size in bytes
    mime_type = db.Column(db.String(100))
    status = db.Column(db.String(20), default='pending', nullable=False)  # pending, uploaded, error
    
    # New fields for categorization
    file_type = db.Column(db.String(50), nullable=False, default='general')  # avatar, post_image, attachment, etc.
    entity_type = db.Column(db.String(50))  # user, post, comment, etc.
    entity_id = db.Column(db.Integer)  # ID of the related entity
    
    is_deleted = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
                          onupdate=lambda: datetime.now(timezone.utc), nullable=False)
    
    # Relationship
    user = db.relationship('User', backref=db.backref('files', lazy='dynamic'))
    
    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "object_name": self.object_name,
            "original_filename": self.original_filename,
            "file_size": self.file_size,
            "mime_type": self.mime_type,
            "status": self.status,
            "file_type": self.file_type,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "url": self.url
        }
    
    @property
    def url(self):
        """Generate a public URL for the file"""
        from flask import current_app
        base_url = current_app.config.get('OSS_PUBLIC_URL', '')
        return f"{base_url}/{self.object_name}" if base_url else None
    
    # Constants for file types
    AVATAR = 'avatar'
    POST_IMAGE = 'post_image'
    COMMENT_ATTACHMENT = 'comment_attachment'
    GENERAL = 'general'
    
    # Constants for entity types
    USER = 'user'
    POST = 'post'
    COMMENT = 'comment'