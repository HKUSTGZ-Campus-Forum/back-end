from aliyunsdkcore.client import AcsClient
from aliyunsdksts.request.v20150401 import AssumeRoleRequest
from oss2 import Auth, Bucket
from app.models.token import STSTokenPool
from app.extensions import db
from datetime import datetime, timedelta, timezone
import json
import uuid # Moved import to top
import os   # Moved import to top
from flask import current_app # Added import
from sqlalchemy.sql import func # Added import

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
                current_app.config['ALIBABA_CLOUD_ACCESS_KEY_ID'], # Use added config
                current_app.config['ALIBABA_CLOUD_ACCESS_KEY_SECRET'], # Use added config
                current_app.config['OSS_REGION_ID']
            )
            
            request = AssumeRoleRequest() # Simplified instantiation
            request.set_RoleArn(current_app.config['OSS_ROLE_ARN'])
            request.set_RoleSessionName(f"pool-token-{uuid.uuid4()}") # Unique session name
            request.set_DurationSeconds(current_app.config['OSS_TOKEN_DURATION'])
            
            response = client.do_action_with_exception(request)
            credentials = json.loads(response.decode("utf-8"))["Credentials"]
            
            expiration_time = datetime.fromisoformat(credentials['Expiration'].replace('Z', '+00:00')).astimezone(timezone.utc) # Ensure timezone aware
            
            token = STSTokenPool(
                access_key_id=credentials['AccessKeyId'],
                access_key_secret=credentials['AccessKeySecret'],
                security_token=credentials['SecurityToken'],
                expiration=expiration_time
            )
            
            db.session.add(token)
            # db.session.commit() # Removed commit from here
            return token
        except Exception as e:
            current_app.logger.error(f"Error generating STS token: {e}")
            db.session.rollback() # Rollback if adding token failed before commit
            return None # Indicate failure

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
    def generate_signed_url(user_id, filename):
        token = OSSService.get_available_token()
        # Handle case where token could not be obtained
        if not token:
             raise Exception("Could not obtain a valid STS token for signing URL.") # Or return an error response

        auth = Auth(token.access_key_id, token.access_key_secret, token.security_token)
        bucket = Bucket(auth, current_app.config['OSS_ENDPOINT'], current_app.config['OSS_BUCKET_NAME'])
        
        # Format timestamp as YYYYMMDD_HHMMSS for better readability
        timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S') # Use UTC time
        file_extension = os.path.splitext(filename)[1] if '.' in filename else ''
        random_name = str(uuid.uuid4())
        
        object_name = f"user_upload/{user_id}/{timestamp}_{random_name}{file_extension}"
        
        try: # Add try/except for signing
            signed_url = bucket.sign_url(
                 "PUT", 
                 object_name, 
                 current_app.config['OSS_TOKEN_DURATION'],
                 headers={'Content-Type': 'application/octet-stream'} # Example header, adjust as needed
            )
            return signed_url, object_name
        except Exception as e:
             current_app.logger.error(f"Error signing OSS URL: {e}")
             raise # Re-raise the exception to be handled by the route

        