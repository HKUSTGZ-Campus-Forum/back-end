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
            # Force pool maintenance before getting token to ensure fresh tokens
            try:
                OSSService.maintain_pool()
            except Exception as maintain_error:
                current_app.logger.warning(f"Pool maintenance failed: {maintain_error}")
            
            token = OSSService.get_available_token()
            if not token:
                current_app.logger.error(f"No valid STS token available for file {self.id}")
                return None
                
            # Check if STS token is about to expire (within 30 minutes for safety)
            now_utc = datetime.now(timezone.utc)
            time_until_token_expiry = (token.expiration - now_utc).total_seconds()
            
            current_app.logger.info(f"STS Token check for file {self.id}: expires in {time_until_token_expiry}s ({time_until_token_expiry/3600:.1f}h)")
            
            if time_until_token_expiry < 1800:  # 30 minutes
                current_app.logger.warning(f"STS token expires in {time_until_token_expiry}s, trying to get a fresher token")
                # Try to get a better token or force regeneration
                try:
                    OSSService.maintain_pool()
                    token = OSSService.get_available_token()
                    if token:
                        time_until_token_expiry = (token.expiration - now_utc).total_seconds()
                        current_app.logger.info(f"Got fresher token: expires in {time_until_token_expiry}s")
                except Exception as e:
                    current_app.logger.error(f"Failed to get fresher token: {e}")
            
            from oss2 import StsAuth, Bucket
            auth = StsAuth(token.access_key_id, token.access_key_secret, token.security_token)
            bucket = Bucket(auth, current_app.config['OSS_ENDPOINT'], current_app.config['OSS_BUCKET_NAME'])
            
            # Calculate URL expiration: Conservative approach
            # Use 1 hour duration, or if token expires sooner, use 70% of remaining token time
            if time_until_token_expiry > 7200:  # Token valid for more than 2 hours
                url_duration_seconds = 3600  # Use 1 hour
            elif time_until_token_expiry > 1800:  # Token valid for 30min - 2 hours
                url_duration_seconds = max(1800, int(time_until_token_expiry * 0.7))  # Use 70% of remaining time, minimum 30 minutes
            else:
                # Token expires soon, use shorter duration
                url_duration_seconds = max(900, int(time_until_token_expiry * 0.5))  # Use 50% of remaining time, minimum 15 minutes
            
            current_app.logger.info(f"Generating signed URL for file {self.id}: duration={url_duration_seconds}s ({url_duration_seconds/3600:.1f}h), token_expires_in={time_until_token_expiry}s")
            
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
            
            # Comprehensive debugging of generated URL
            import urllib.parse as urlparse
            parsed_url = urlparse.urlparse(signed_url)
            query_params = urlparse.parse_qs(parsed_url.query)
            
            if 'Expires' in query_params:
                expires_timestamp = int(query_params['Expires'][0])
                expires_datetime = datetime.fromtimestamp(expires_timestamp, tz=timezone.utc)
                current_time_timestamp = int(now_utc.timestamp())
                
                current_app.logger.info(f"URL Generation Debug for file {self.id}:")
                current_app.logger.info(f"  Current time: {now_utc.isoformat()} (timestamp: {current_time_timestamp})")
                current_app.logger.info(f"  URL expires at: {expires_datetime.isoformat()} (timestamp: {expires_timestamp})")
                current_app.logger.info(f"  URL valid for: {expires_timestamp - current_time_timestamp}s")
                
                # Verify the URL isn't already expired
                if expires_timestamp <= current_time_timestamp:
                    current_app.logger.error(f"CRITICAL: Generated URL is already expired! expires={expires_timestamp}, current={current_time_timestamp}")
                    # Try with minimal duration
                    url_duration_seconds = 900  # 15 minutes
                    current_app.logger.info(f"Retrying with minimal duration: {url_duration_seconds}s")
                    signed_url = bucket.sign_url("GET", self.object_name, url_duration_seconds, headers=headers)
            else:
                current_app.logger.warning(f"No expiration timestamp found in generated URL for file {self.id}")
            
            return signed_url
            
        except Exception as e:
            current_app.logger.error(f"Failed to generate signed URL for file {self.id}: {e}")
            import traceback
            current_app.logger.error(f"Full traceback: {traceback.format_exc()}")
        
        # Fallback to direct URL (for public buckets)
        base_url = current_app.config.get('OSS_PUBLIC_URL', '')
        return f"{base_url}/{self.object_name}" if base_url else None