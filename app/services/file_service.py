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
        # Get a random valid token from the pool
        valid_token = STSTokenPool.query.filter(
            STSTokenPool.expiration > datetime.now(timezone.utc) + timedelta(minutes=5)
        ).order_by(func.random()).first() # Use imported func
        
        # Check if pool needs initial population or regeneration
        if not valid_token:
             # Attempt to generate a new token if none are valid or pool is empty
             valid_token = OSSService._generate_new_token()
             if not valid_token:
                 # Handle case where token generation fails (e.g., log error, raise exception)
                 current_app.logger.error("Failed to generate a new STS token.")
                 # Depending on requirements, you might return None or raise an error
                 return None # Or raise appropriate exception

        return valid_token


    @staticmethod
    def _generate_new_token():
        # Generate and store new STS token
        try: # Added try/except for robustness
            client = AcsClient(
                current_app.config['ALIBABA_CLOUD_ACCESS_KEY_ID'], 
                current_app.config['ALIBABA_CLOUD_ACCESS_KEY_SECRET'], 
                current_app.config['OSS_REGION_ID']
            )
            
            # Fix: Use AssumeRoleRequest.AssumeRoleRequest() instead of AssumeRoleRequest()
            request = AssumeRoleRequest.AssumeRoleRequest()
            request.set_RoleArn(current_app.config['OSS_ROLE_ARN'])
            request.set_RoleSessionName(f"pool-token-{uuid.uuid4()}") # Unique session name
            request.set_DurationSeconds(current_app.config['OSS_TOKEN_DURATION'])
            
            response = client.do_action_with_exception(request)
            credentials = json.loads(response.decode("utf-8"))["Credentials"]
            
            expiration_time = datetime.fromisoformat(credentials['Expiration'].replace('Z', '+00:00')).astimezone(timezone.utc)
            
            token = STSTokenPool(
                access_key_id=credentials['AccessKeyId'],
                access_key_secret=credentials['AccessKeySecret'],
                security_token=credentials['SecurityToken'],
                expiration=expiration_time
            )
            
            db.session.add(token)
            return token
        except Exception as e:
            current_app.logger.error(f"Error generating STS token: {e}")
            db.session.rollback()
            return None

    @staticmethod
    def maintain_pool():
        # Cleanup expired tokens
        deleted_count = STSTokenPool.query.filter(
            STSTokenPool.expiration <= datetime.now(timezone.utc)
        ).delete(synchronize_session=False) # Added synchronize_session=False for bulk delete efficiency
        if deleted_count > 0:
             current_app.logger.info(f"Cleaned up {deleted_count} expired STS tokens.")

        # Generate new tokens if below minimum pool size
        # Use count() outside the loop for efficiency
        current_count = STSTokenPool.query.count() 
        tokens_to_add = OSSService.MIN_POOL_SIZE - current_count
        
        if tokens_to_add > 0:
             current_app.logger.info(f"STS token pool below minimum ({current_count}/{OSSService.MIN_POOL_SIZE}). Adding {tokens_to_add} tokens.")
             for _ in range(tokens_to_add):
                 new_token = OSSService._generate_new_token()
                 if not new_token:
                      current_app.logger.warning("Failed to generate a token during pool maintenance.")
                      # Decide if you want to break or continue trying
                      break 
        # Commit happens in the calling task function (sts_pool.maintain_pool)

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
            entity_id=entity_id       # Save entity_id
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

        