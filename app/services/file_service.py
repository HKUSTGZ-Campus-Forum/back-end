from aliyunsdkcore.client import AcsClient
from aliyunsdksts.request.v20150401 import AssumeRoleRequest
from oss2 import Auth, StsAuth, Bucket
from sqlalchemy import and_, or_
from app.models.token import STSTokenPool
from app.extensions import db
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
import json
import uuid # Moved import to top
import os   # Moved import to top
import base64 # Added missing import
from flask import current_app # Added import
from app.models.file import File # Ensure File is imported


class UploadVerificationError(Exception):
    """Raised when an uploaded OSS object does not match the file contract."""


@dataclass(frozen=True)
class FileDeletionResult:
    """Outcome of removing an unbound upload from the user's draft."""

    record: File
    cleanup_pending: bool


class OSSService:
    MIN_POOL_SIZE = 10
    MAX_POOL_SIZE = 20

    @staticmethod
    def _has_sts_config():
        required_keys = [
            'ALIBABA_CLOUD_ACCESS_KEY_ID',
            'ALIBABA_CLOUD_ACCESS_KEY_SECRET',
            'OSS_ROLE_ARN',
            'OSS_REGION_ID'
        ]
        return all(current_app.config.get(key) for key in required_keys)

    @staticmethod
    def _create_upload_bucket():
        endpoint = current_app.config.get('OSS_ENDPOINT')
        bucket_name = current_app.config.get('OSS_BUCKET_NAME')

        if not endpoint or not bucket_name:
            raise Exception('OSS endpoint or bucket name is not configured.')

        token = None
        try:
            token = OSSService.get_available_token()
        except Exception as e:
            current_app.logger.error(f'STS token lookup failed, falling back to direct OSS signing: {e}')

        if token:
            auth = StsAuth(token.access_key_id, token.access_key_secret, token.security_token)
            return Bucket(auth, endpoint, bucket_name)

        access_key_id = current_app.config.get('ALIBABA_CLOUD_ACCESS_KEY_ID')
        access_key_secret = current_app.config.get('ALIBABA_CLOUD_ACCESS_KEY_SECRET')
        if not access_key_id or not access_key_secret:
            raise Exception('No usable OSS credentials available for signing upload URL.')

        current_app.logger.warning('STS unavailable, falling back to long-lived OSS credentials for upload signing.')
        auth = Auth(access_key_id, access_key_secret)
        return Bucket(auth, endpoint, bucket_name)

    @staticmethod
    def _create_management_bucket():
        """Create a server-owned OSS client for HEAD/delete operations.

        Browser upload STS credentials are intentionally narrow and may not
        include DeleteObject. Cleanup must therefore use the server's managed
        credentials instead of whichever upload token happens to be in the
        shared token pool.
        """
        endpoint = current_app.config.get('OSS_ENDPOINT')
        bucket_name = current_app.config.get('OSS_BUCKET_NAME')
        access_key_id = current_app.config.get('ALIBABA_CLOUD_ACCESS_KEY_ID')
        access_key_secret = current_app.config.get('ALIBABA_CLOUD_ACCESS_KEY_SECRET')

        if not endpoint or not bucket_name:
            raise Exception('OSS endpoint or bucket name is not configured.')
        if not access_key_id or not access_key_secret:
            raise Exception('OSS management credentials are not configured.')

        return Bucket(Auth(access_key_id, access_key_secret), endpoint, bucket_name)

    @staticmethod
    def object_exists(object_name):
        """Check whether a browser-uploaded object is present in OSS."""
        if not object_name:
            return False
        try:
            return bool(OSSService._create_upload_bucket().object_exists(object_name))
        except Exception as e:
            current_app.logger.error(
                f"Failed to verify OSS object '{object_name}': {e}"
            )
            return False

    @staticmethod
    def get_available_token(min_validity_seconds=900):
        if not OSSService._has_sts_config():
            current_app.logger.info('STS configuration incomplete, skipping STS token pool lookup.')
            return None

        # Get a token with configurable minimum remaining validity
        min_validity_seconds = max(60, int(min_validity_seconds or 0))
        min_expiration = datetime.now(timezone.utc) + timedelta(seconds=min_validity_seconds)
        valid_token = STSTokenPool.query.filter(
            STSTokenPool.expiration > min_expiration
        ).order_by(STSTokenPool.expiration.desc()).first()
        
        # Log current token pool status
        total_tokens = STSTokenPool.query.count()
        valid_tokens_count = STSTokenPool.query.filter(
            STSTokenPool.expiration > min_expiration
        ).count()

        current_app.logger.info(
            f"STS Token Pool Status: {valid_tokens_count}/{total_tokens} tokens valid for {min_validity_seconds}+ seconds"
        )
        
        # Check if pool needs initial population or regeneration
        if not valid_token:
            current_app.logger.warning("No valid STS tokens available, generating new token")
            # Attempt to generate a new token if none are valid or pool is empty
            valid_token = OSSService._generate_new_token()
            if valid_token:
                # Commit the new token immediately
                try:
                    db.session.commit()
                    current_app.logger.info(f"Generated new STS token, expires at {valid_token.expiration.isoformat()}")
                except Exception as e:
                    db.session.rollback()
                    current_app.logger.error(f"Failed to commit new STS token: {e}")
                    return None
            else:
                # Handle case where token generation fails (e.g., log error, raise exception)
                current_app.logger.error("Failed to generate a new STS token.")
                return None

        if valid_token:
            time_until_expiry = (valid_token.expiration - datetime.now(timezone.utc)).total_seconds()
            current_app.logger.info(f"Using STS token that expires in {time_until_expiry}s")

        return valid_token


    @staticmethod
    def _generate_new_token():
        # Generate and store new STS token
        try: # Added try/except for robustness
            current_app.logger.info("Generating new STS token...")
            
            client = AcsClient(
                current_app.config['ALIBABA_CLOUD_ACCESS_KEY_ID'], 
                current_app.config['ALIBABA_CLOUD_ACCESS_KEY_SECRET'], 
                current_app.config['OSS_REGION_ID']
            )
            
            # Fix: Use AssumeRoleRequest.AssumeRoleRequest() instead of AssumeRoleRequest()
            request = AssumeRoleRequest.AssumeRoleRequest()
            request.set_RoleArn(current_app.config['OSS_ROLE_ARN'])
            request.set_RoleSessionName(f"pool-token-{uuid.uuid4()}") # Unique session name
            
            # Use configuration value for token duration
            token_duration = current_app.config['OSS_TOKEN_DURATION']
            request.set_DurationSeconds(token_duration)
            
            current_app.logger.info(f"Requesting STS token with {token_duration}s duration")
            
            response = client.do_action_with_exception(request)
            credentials = json.loads(response.decode("utf-8"))["Credentials"]
            
            # More robust timezone handling
            expiration_str = credentials['Expiration']
            current_app.logger.info(f"Received STS token expiration: {expiration_str}")
            
            # Handle different expiration formats
            if expiration_str.endswith('Z'):
                # ISO format with Z suffix
                expiration_time = datetime.fromisoformat(expiration_str.replace('Z', '+00:00'))
            elif '+' in expiration_str or expiration_str.endswith('UTC'):
                # Already includes timezone info
                expiration_time = datetime.fromisoformat(expiration_str.replace('UTC', '+00:00'))
            else:
                # Assume UTC if no timezone specified
                expiration_time = datetime.fromisoformat(expiration_str).replace(tzinfo=timezone.utc)
            
            # Ensure UTC timezone
            if expiration_time.tzinfo != timezone.utc:
                expiration_time = expiration_time.astimezone(timezone.utc)
            
            current_app.logger.info(f"Parsed STS token expiration as UTC: {expiration_time.isoformat()}")
            
            # Verify the token will be valid for a reasonable amount of time
            time_until_expiry = (expiration_time - datetime.now(timezone.utc)).total_seconds()
            if time_until_expiry < 1800:  # 30 minutes
                current_app.logger.warning(f"STS token expires in only {time_until_expiry}s, this may cause issues")
            
            token = STSTokenPool(
                access_key_id=credentials['AccessKeyId'],
                access_key_secret=credentials['AccessKeySecret'],
                security_token=credentials['SecurityToken'],
                expiration=expiration_time
            )
            
            db.session.add(token)
            current_app.logger.info(f"Created STS token record, expires in {time_until_expiry}s")
            return token
            
        except Exception as e:
            current_app.logger.error(f"Error generating STS token: {e}")
            import traceback
            current_app.logger.error(f"Full traceback: {traceback.format_exc()}")
            db.session.rollback()
            return None

    @staticmethod
    def maintain_pool():
        if not OSSService._has_sts_config():
            current_app.logger.info('Skipping STS pool maintenance because STS configuration is incomplete.')
            return

        # Cleanup expired tokens
        deleted_count = STSTokenPool.query.filter(
            STSTokenPool.expiration <= datetime.now(timezone.utc)
        ).delete(synchronize_session=False) # Added synchronize_session=False for bulk delete efficiency
        if deleted_count > 0:
             current_app.logger.info(f"Cleaned up {deleted_count} expired STS tokens.")

        # Also cleanup tokens that expire within 15 minutes (too close to expiry)
        soon_expired_count = STSTokenPool.query.filter(
            STSTokenPool.expiration <= datetime.now(timezone.utc) + timedelta(minutes=15)
        ).delete(synchronize_session=False)
        if soon_expired_count > 0:
             current_app.logger.info(f"Cleaned up {soon_expired_count} STS tokens expiring within 15 minutes.")

        # Count valid tokens (those with 15+ minutes remaining)
        valid_count = STSTokenPool.query.filter(
            STSTokenPool.expiration > datetime.now(timezone.utc) + timedelta(minutes=15)
        ).count()
        
        tokens_to_add = OSSService.MIN_POOL_SIZE - valid_count
        
        if tokens_to_add > 0:
             current_app.logger.info(f"STS token pool below minimum valid tokens ({valid_count}/{OSSService.MIN_POOL_SIZE}). Adding {tokens_to_add} tokens.")
             
             successful_additions = 0
             for i in range(tokens_to_add):
                 current_app.logger.info(f"Generating token {i+1}/{tokens_to_add}")
                 new_token = OSSService._generate_new_token()
                 if new_token:
                     successful_additions += 1
                     # Commit each token individually to ensure persistence
                     try:
                         db.session.commit()
                         current_app.logger.info(f"Successfully added token {successful_additions}/{tokens_to_add}")
                     except Exception as e:
                         db.session.rollback()
                         current_app.logger.error(f"Failed to commit token {i+1}: {e}")
                 else:
                      current_app.logger.warning(f"Failed to generate token {i+1}/{tokens_to_add} during pool maintenance.")
                      # Continue trying to generate remaining tokens
             
             current_app.logger.info(f"Pool maintenance complete: {successful_additions}/{tokens_to_add} tokens added successfully")
        else:
            current_app.logger.info(f"STS token pool is healthy: {valid_count} valid tokens available")

    @staticmethod
    def generate_signed_url(user_id, filename, file_type=File.GENERAL, entity_type=None, entity_id=None, callback_url=None, content_type=None): # Added categorization params
        bucket = OSSService._create_upload_bucket()

        # Format timestamp as YYYYMMDD_HHMMSS for better readability
        timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
        file_extension = os.path.splitext(filename)[1] if '.' in filename else ''
        random_name = str(uuid.uuid4())

        # Include file_type in the object path for better organization in OSS
        object_name = f"user_upload/{user_id}/{timestamp}_{random_name}{file_extension}"

        # Create a file record in pending status with categorization
        file_record = File(
            user_id=user_id,
            object_name=object_name,
            original_filename=filename,
            status='pending',
            file_type=file_type,      # Save file_type
            entity_type=entity_type,  # Save entity_type
            entity_id=entity_id,      # Save entity_id
            mime_type=content_type    # Save MIME type from frontend
        )
        db.session.add(file_record)
        # Commit here to get the file_record.id for the callback
        try:
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Database error creating file record for user {user_id}: {e}")
            raise Exception("Failed to create file record before signing URL.")


        # Temporarily disable callback to test basic signed URL functionality
        callback_params = None
        # TODO: Re-implement callback after basic upload works
        # if callback_url:
        #     # Callback implementation here...

        try:
            # Generate the signed URL with proper headers
            # Important: Headers used during signing must match headers sent by client
            headers = {}
            if content_type:
                headers['Content-Type'] = content_type
            
            signed_url = bucket.sign_url(
                "PUT",
                object_name,
                current_app.config['OSS_TOKEN_DURATION'],
                headers=headers
            )
            current_app.logger.info(f"Generated signed URL for user {user_id}, file_id {file_record.id}, object {object_name}")
            return signed_url, object_name, file_record.id
        except Exception as e:
            current_app.logger.error(f"Error signing OSS URL for user {user_id}, file_id {file_record.id}: {e}")
            # Attempt to mark the file record as error, but don't let this fail the request
            try:
                 file_record = File.query.get(file_record.id)
                 if file_record:
                     file_record.status = 'error'
                     db.session.commit()
            except Exception as db_err:
                 db.session.rollback()
                 current_app.logger.error(f"Failed to mark file record {file_record.id} as error: {db_err}")
            raise # Re-raise the signing exception

    @staticmethod
    def max_upload_bytes(file_type, mime_type=None):
        """Return the authoritative byte limit for a file category."""
        normalized_mime = (mime_type or '').lower()
        if file_type == File.POST_ATTACHMENT and normalized_mime.startswith('video/'):
            return File.MAX_VIDEO_UPLOAD_BYTES
        return File.MAX_UPLOAD_BYTES

    @staticmethod
    def complete_upload(file_id, user_id):
        """Verify the OSS object before promoting a pending upload.

        This endpoint-driven confirmation replaces the old GET-side status
        mutation. The size and MIME type persisted here come from OSS HEAD, not
        from the browser's declaration.
        """
        file_record = File.query.filter_by(
            id=file_id,
            user_id=user_id,
            is_deleted=False,
        ).first()
        if not file_record:
            return None

        if file_record.status == 'error':
            raise UploadVerificationError('Upload is already marked as failed')

        try:
            bucket = OSSService._create_upload_bucket()
            metadata = bucket.head_object(file_record.object_name)
            actual_size = int(getattr(metadata, 'content_length', 0) or 0)
            actual_mime = (
                getattr(metadata, 'content_type', None)
                or getattr(metadata, 'headers', {}).get('Content-Type')
                or file_record.mime_type
                or 'application/octet-stream'
            ).split(';', 1)[0].strip().lower()

            if actual_size <= 0:
                raise UploadVerificationError('Uploaded object is empty')

            max_bytes = OSSService.max_upload_bytes(file_record.file_type, actual_mime)
            if actual_size > max_bytes:
                raise UploadVerificationError(
                    f'File exceeds maximum size of {max_bytes} bytes'
                )

            if file_record.file_type == File.POST_IMAGE and not actual_mime.startswith('image/'):
                raise UploadVerificationError('Image upload has an invalid content type')
            if file_record.file_type == File.POST_IMAGE and actual_mime == 'image/svg+xml':
                raise UploadVerificationError('SVG images are not supported')

            file_record.file_size = actual_size
            file_record.mime_type = actual_mime
            file_record.status = 'uploaded'
            db.session.commit()
            current_app.logger.info(
                f'Verified OSS upload for file_id {file_id}: {actual_size} bytes, {actual_mime}'
            )
            return file_record
        except UploadVerificationError:
            file_record.status = 'error'
            db.session.commit()
            try:
                OSSService._create_management_bucket().delete_object(file_record.object_name)
            except Exception as cleanup_error:
                current_app.logger.warning(
                    f'Could not remove rejected OSS object for file_id {file_id}: {cleanup_error}'
                )
            raise
        except Exception as error:
            db.session.rollback()
            current_app.logger.error(
                f'OSS verification failed for file_id {file_id}: {error}',
                exc_info=True,
            )
            raise UploadVerificationError('Uploaded object could not be verified') from error

    @staticmethod
    def update_file_status(file_id, status, object_name=None, file_size=None, mime_type=None): # Added object_name for verification
        """Update file status after callback"""
        file_record = File.query.get(file_id)
        if file_record:
             # Optional verification: Check if object_name from callback matches record
             if object_name and file_record.object_name != object_name:
                 current_app.logger.warning(f"Callback object name mismatch for file_id {file_id}. Expected '{file_record.object_name}', got '{object_name}'.")
                 # Decide how to handle mismatch - log, set status to error, etc.
                 # For now, we'll proceed but log a warning.

             file_record.status = status
             if file_size is not None: # Check for None explicitly
                 try:
                     sz = int(file_size)
                     file_record.file_size = sz
                     max_bytes = OSSService.max_upload_bytes(
                         file_record.file_type,
                         mime_type or file_record.mime_type,
                     )
                     if sz > max_bytes:
                         current_app.logger.warning(
                             f"Uploaded file {file_id} exceeds max size ({sz} > {max_bytes}), marking error."
                         )
                         file_record.status = 'error'
                 except (ValueError, TypeError):
                      current_app.logger.warning(f"Invalid file size '{file_size}' received for file_id {file_id}.")
                      file_record.file_size = None # Or 0, or keep existing if any
             if mime_type:
                 file_record.mime_type = mime_type

             try:
                 db.session.commit()
                 current_app.logger.info(f"Updated file status to '{status}' for file_id {file_id}.")
                 return file_record
             except Exception as e:
                 db.session.rollback()
                 current_app.logger.error(f"Database error updating file status for file_id {file_id}: {e}")
                 return None # Indicate failure
        else:
             current_app.logger.warning(f"File record not found for file_id {file_id} during callback processing.")
             return None

    @staticmethod
    def delete_file(file_id, user_id):
        """Remove an unbound draft upload and queue cleanup when OSS is unavailable."""
        file_record = File.query.filter_by(id=file_id, user_id=user_id, is_deleted=False).first()
        if not file_record:
            return None # Not found or already deleted

        if file_record.entity_type == 'post' and file_record.entity_id is not None:
            return None

        try:
            OSSService._create_management_bucket().delete_object(file_record.object_name)
        except Exception as e:
            current_app.logger.warning(
                f"Deferred OSS cleanup for file_id {file_id}: {e}",
            )
            file_record.status = 'error'
            try:
                db.session.commit()
                return FileDeletionResult(record=file_record, cleanup_pending=True)
            except Exception as db_error:
                db.session.rollback()
                current_app.logger.error(
                    f"Could not queue cleanup for file_id {file_id}: {db_error}",
                    exc_info=True,
                )
                return None

        file_record.is_deleted = True
        file_record.deleted_at = datetime.now(timezone.utc)

        try:
            db.session.commit()
            current_app.logger.info(f"Deleted OSS object and soft-deleted file_id {file_id}.")
            return FileDeletionResult(record=file_record, cleanup_pending=False)
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Database error soft deleting file record for file_id {file_id}: {e}")
            return None # Indicate failure

    @staticmethod
    def cleanup_stale_unbound_uploads(max_age_hours=24):
        """Remove abandoned objects that were never attached.

        Pending and failed uploads are always eligible. Verified forum uploads are
        also eligible because a user can remove them from an unpublished draft
        while the immediate OSS cleanup request is unavailable.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
        stale_files = File.query.filter(
            File.entity_id.is_(None),
            File.is_deleted.is_(False),
            or_(
                File.status.in_(('pending', 'error')),
                and_(
                    File.status == 'uploaded',
                    File.entity_type == 'post',
                    File.file_type.in_((File.POST_IMAGE, File.POST_ATTACHMENT)),
                ),
            ),
            File.created_at < cutoff,
        ).limit(200).all()
        if not stale_files:
            return 0

        bucket = OSSService._create_management_bucket()
        cleaned = 0
        for file_record in stale_files:
            try:
                bucket.delete_object(file_record.object_name)
                file_record.is_deleted = True
                file_record.deleted_at = datetime.now(timezone.utc)
                cleaned += 1
            except Exception as error:
                current_app.logger.warning(
                    f'Could not clean stale upload file_id {file_record.id}: {error}'
                )
        db.session.commit()
        return cleaned

        
