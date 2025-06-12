from aliyunsdkcore.client import AcsClient
from aliyunsdksts.request.v20150401 import AssumeRoleRequest
from oss2 import StsAuth, Bucket
from app.models.token import STSTokenPool
from app.extensions import db
from datetime import datetime, timedelta, timezone
import json
import uuid # Moved import to top
import os   # Moved import to top
import base64 # Added missing import
from flask import current_app # Added import
from sqlalchemy.sql import func # Added import
from app.models.file import File # Ensure File is imported

class OSSService:
    MIN_POOL_SIZE = 10
    MAX_POOL_SIZE = 20

    @staticmethod
    def get_available_token():
        # Get a token with at least 30 minutes remaining (increased for avatar URL generation)
        now_utc = datetime.now(timezone.utc)
        min_expiration = now_utc + timedelta(minutes=30)
        valid_token = STSTokenPool.query.filter(
            STSTokenPool.expiration > min_expiration
        ).order_by(STSTokenPool.expiration.desc()).first()  # Get the token that expires latest
        
        # Log current token pool status
        total_tokens = STSTokenPool.query.count()
        valid_tokens_count = STSTokenPool.query.filter(
            STSTokenPool.expiration > min_expiration
        ).count()
        
        current_app.logger.info(f"STS Token Pool Status: {valid_tokens_count}/{total_tokens} tokens valid for 30+ minutes")
        
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
            time_until_expiry = (valid_token.expiration - now_utc).total_seconds()
            current_app.logger.info(f"Using STS token ID {valid_token.id} that expires in {time_until_expiry}s ({time_until_expiry/3600:.1f}h)")
            
            # Double-check the token isn't about to expire
            if time_until_expiry < 1800:  # Less than 30 minutes
                current_app.logger.warning(f"Selected token expires soon ({time_until_expiry}s), will try to generate a fresh one")
                fresh_token = OSSService._generate_new_token()
                if fresh_token:
                    try:
                        db.session.commit()
                        current_app.logger.info(f"Generated fresh STS token, expires at {fresh_token.expiration.isoformat()}")
                        return fresh_token
                    except Exception as e:
                        db.session.rollback()
                        current_app.logger.error(f"Failed to commit fresh STS token: {e}")
                # Fall back to the original token if fresh generation fails
                current_app.logger.warning("Using the originally selected token despite short expiry")

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
        now_utc = datetime.now(timezone.utc)
        current_app.logger.info(f"Starting STS pool maintenance at {now_utc.isoformat()}")
        
        # Cleanup expired tokens
        deleted_count = STSTokenPool.query.filter(
            STSTokenPool.expiration <= now_utc
        ).delete(synchronize_session=False)
        if deleted_count > 0:
             current_app.logger.info(f"Cleaned up {deleted_count} expired STS tokens.")

        # Also cleanup tokens that expire within 30 minutes (too close to expiry for avatar generation)
        soon_expired_count = STSTokenPool.query.filter(
            STSTokenPool.expiration <= now_utc + timedelta(minutes=30)
        ).delete(synchronize_session=False)
        if soon_expired_count > 0:
             current_app.logger.info(f"Cleaned up {soon_expired_count} STS tokens expiring within 30 minutes.")

        # Commit cleanup changes
        try:
            db.session.commit()
            current_app.logger.info("Token cleanup committed successfully")
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Failed to commit token cleanup: {e}")

        # Count valid tokens (those with 30+ minutes remaining for avatar generation)
        valid_count = STSTokenPool.query.filter(
            STSTokenPool.expiration > now_utc + timedelta(minutes=30)
        ).count()
        
        tokens_to_add = max(OSSService.MIN_POOL_SIZE - valid_count, 0)
        
        if tokens_to_add > 0:
             current_app.logger.info(f"STS token pool below minimum valid tokens ({valid_count}/{OSSService.MIN_POOL_SIZE}). Adding {tokens_to_add} tokens.")
             
             successful_additions = 0
             for i in range(tokens_to_add):
                 current_app.logger.info(f"Generating token {i+1}/{tokens_to_add}")
                 new_token = OSSService._generate_new_token()
                 if new_token:
                     # Commit each token individually to ensure persistence
                     try:
                         db.session.commit()
                         successful_additions += 1
                         current_app.logger.info(f"Successfully added token {successful_additions}/{tokens_to_add}, expires: {new_token.expiration.isoformat()}")
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
        token = OSSService.get_available_token()
        # Handle case where token could not be obtained
        if not token:
             current_app.logger.error(f"User {user_id} failed to obtain STS token.")
             raise Exception("Could not obtain a valid STS token for signing URL.")

        auth = StsAuth(token.access_key_id, token.access_key_secret, token.security_token)
        bucket = Bucket(auth, current_app.config['OSS_ENDPOINT'], current_app.config['OSS_BUCKET_NAME'])

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
                     file_record.file_size = int(file_size)
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
        """Soft delete a file record and potentially delete from OSS."""
        file_record = File.query.filter_by(id=file_id, user_id=user_id, is_deleted=False).first()
        if not file_record:
            return None # Not found or already deleted

        file_record.is_deleted = True

        # TODO: Implement actual deletion from OSS (potentially in a background task)
        # try:
        #     token = OSSService.get_available_token()
        #     auth = StsAuth(token.access_key_id, token.access_key_secret, token.security_token)
        #     bucket = Bucket(auth, current_app.config['OSS_ENDPOINT'], current_app.config['OSS_BUCKET_NAME'])
        #     bucket.delete_object(file_record.object_name)
        #     current_app.logger.info(f"Deleted object {file_record.object_name} from OSS for file_id {file_id}.")
        # except Exception as e:
        #     current_app.logger.error(f"Failed to delete object {file_record.object_name} from OSS for file_id {file_id}: {e}")
        #     # Decide if the DB soft delete should be rolled back or not
        #     # db.session.rollback()
        #     # return None # Indicate failure

        try:
            db.session.commit()
            current_app.logger.info(f"Soft deleted file record for file_id {file_id}.")
            return file_record
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Database error soft deleting file record for file_id {file_id}: {e}")
            return None # Indicate failure

        