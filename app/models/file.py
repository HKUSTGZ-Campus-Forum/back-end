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
        from datetime import datetime, timezone
        import time
        
        # For private buckets, generate signed URLs for viewing
        try:
            token = OSSService.get_available_token()
            if not token:
                current_app.logger.error(f"No valid STS token available for file {self.id}")
                return None
                
            # Check if STS token is about to expire (within 10 minutes)
            time_until_token_expiry = (token.expiration - datetime.now(timezone.utc)).total_seconds()
            if time_until_token_expiry < 600:  # 10 minutes
                current_app.logger.warning(f"STS token expires in {time_until_token_expiry}s, may cause URL generation issues")
            
            from oss2 import StsAuth, Bucket
            auth = StsAuth(token.access_key_id, token.access_key_secret, token.security_token)
            bucket = Bucket(auth, current_app.config['OSS_ENDPOINT'], current_app.config['OSS_BUCKET_NAME'])
            
            # Calculate URL expiration: use the shorter of 1 hour or remaining STS token time minus 5 minutes buffer
            url_duration_seconds = min(
                3600,  # 1 hour maximum
                max(1800, int(time_until_token_expiry - 300))  # At least 30 minutes, but respect token expiry with 5min buffer
            )
            
            current_app.logger.info(f"Generating signed URL for file {self.id} with {url_duration_seconds}s duration (token expires in {time_until_token_expiry}s)")
            
            # Set headers to display inline instead of downloading
            headers = {}
            if self.mime_type and self.mime_type.startswith('image/'):
                headers['response-content-type'] = self.mime_type
                headers['response-content-disposition'] = 'inline'
            elif self.mime_type:
                headers['response-content-type'] = self.mime_type
                headers['response-content-disposition'] = f'inline; filename="{self.original_filename}"'
            else:
                # Default to image content type if not set
                headers['response-content-type'] = 'image/png'
                headers['response-content-disposition'] = 'inline'
            
            signed_url = bucket.sign_url("GET", self.object_name, url_duration_seconds, headers=headers)
            
            # Force HTTPS for the signed URL
            if signed_url.startswith('http://'):
                signed_url = 'https://' + signed_url[7:]
            
            # Log URL expiration for debugging
            import urllib.parse as urlparse
            parsed_url = urlparse.urlparse(signed_url)
            query_params = urlparse.parse_qs(parsed_url.query)
            if 'Expires' in query_params:
                expires_timestamp = int(query_params['Expires'][0])
                expires_datetime = datetime.fromtimestamp(expires_timestamp, tz=timezone.utc)
                current_app.logger.info(f"Generated signed URL for file {self.id} expires at {expires_datetime.isoformat()}")
            
            return signed_url
            
        except Exception as e:
            current_app.logger.error(f"Failed to generate signed URL for file {self.id}: {e}")
            import traceback
            current_app.logger.error(f"Full traceback: {traceback.format_exc()}")
        
        # Fallback to direct URL (for public buckets)
        base_url = current_app.config.get('OSS_PUBLIC_URL', '')
        if base_url:
            # Ensure base_url uses HTTPS
            if base_url.startswith('http://'):
                base_url = 'https://' + base_url[7:]
            return f"{base_url}/{self.object_name}"
        return None