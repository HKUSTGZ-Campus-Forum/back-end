from datetime import datetime, timezone
from app.extensions import db

class File(db.Model):
    __tablename__ = 'files'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False) # Assumes you have this FK
    object_name = db.Column(db.String(512), nullable=False, unique=True)
    original_filename = db.Column(db.String(255), nullable=False)
    file_size = db.Column(db.BigInteger) # Use BigInteger for potentially large files
    mime_type = db.Column(db.String(100))
    status = db.Column(db.String(20), default='pending', nullable=False, index=True) # e.g., pending, uploaded, error
    file_type = db.Column(db.String(50), default='general', nullable=False, index=True) # e.g., avatar, post_image, general
    entity_type = db.Column(db.String(50), nullable=True, index=True) # e.g., post, comment
    entity_id = db.Column(db.Integer, nullable=True, index=True)
    is_deleted = db.Column(db.Boolean, default=False, nullable=False)
    deleted_at = db.Column(db.DateTime(timezone=True))
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    # Constants for file types (example)
    AVATAR = 'avatar'
    POST_IMAGE = 'post_image'
    COMMENT_ATTACHMENT = 'comment_attachment'
    GENERAL = 'general'

    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'object_name': self.object_name,
            'original_filename': self.original_filename,
            'file_size': self.file_size,
            'mime_type': self.mime_type,
            'status': self.status,
            'file_type': self.file_type,
            'entity_type': self.entity_type,
            'entity_id': self.entity_id,
            'is_deleted': self.is_deleted,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'url': self.url # Assuming you have the url property
        }

    @property
    def url(self):
        """Generate a signed URL for viewing the file"""
        from flask import current_app
        from app.services.file_service import OSSService
        
        # For private buckets, generate signed URLs for viewing
        try:
            token = OSSService.get_available_token()
            if token:
                from oss2 import StsAuth, Bucket
                auth = StsAuth(token.access_key_id, token.access_key_secret, token.security_token)
                bucket = Bucket(auth, current_app.config['OSS_ENDPOINT'], current_app.config['OSS_BUCKET_NAME'])
                
                # Generate signed URL for GET (viewing) - valid for 1 hour
                # Set headers to display inline instead of downloading
                headers = {}
                if self.mime_type:
                    headers['response-content-type'] = self.mime_type
                headers['response-content-disposition'] = f'inline; filename="{self.original_filename}"'
                
                signed_url = bucket.sign_url("GET", self.object_name, 3600, headers=headers)
                return signed_url
        except Exception as e:
            current_app.logger.error(f"Failed to generate signed URL for file {self.id}: {e}")
        
        # Fallback to direct URL (for public buckets)
        base_url = current_app.config.get('OSS_PUBLIC_URL', '')
        return f"{base_url}/{self.object_name}" if base_url else None