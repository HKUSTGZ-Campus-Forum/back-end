from aliyunsdkcore.client import AcsClient
from aliyunsdksts.request.v20150401 import AssumeRoleRequest
from oss2 import Auth, Bucket
from app.models.token import STSTokenPool
from app.extensions import db
from datetime import datetime, timedelta, timezone
import json
import uuid
import os

class OSSService:
    MIN_POOL_SIZE = 10
    MAX_POOL_SIZE = 20

    @staticmethod
    def get_available_token():
        # Get a random valid token from the pool
        valid_token = STSTokenPool.query.filter(
            STSTokenPool.expiration > datetime.now(timezone.utc) + timedelta(minutes=5)
        ).order_by(func.random()).first()
        
        return valid_token or OSSService._generate_new_token()

    @staticmethod
    def _generate_new_token():
        # Generate and store new STS token
        client = AcsClient(
            current_app.config['ALIBABA_CLOUD_ACCESS_KEY_ID'],
            current_app.config['ALIBABA_CLOUD_ACCESS_KEY_SECRET'],
            current_app.config['OSS_REGION_ID']
        )
        
        request = AssumeRoleRequest.AssumeRoleRequest()
        request.set_RoleArn(current_app.config['OSS_ROLE_ARN'])
        request.set_RoleSessionName("pool-token")
        request.set_DurationSeconds(current_app.config['OSS_TOKEN_DURATION'])
        
        response = client.do_action_with_exception(request)
        credentials = json.loads(response.decode("utf-8"))["Credentials"]
        
        expiration_time = datetime.fromisoformat(credentials['Expiration']).astimezone(timezone.utc)
        
        token = STSTokenPool(
            access_key_id=credentials['AccessKeyId'],
            access_key_secret=credentials['AccessKeySecret'],
            security_token=credentials['SecurityToken'],
            expiration=expiration_time
        )
        
        db.session.add(token)
        db.session.commit()
        return token

    @staticmethod
    def maintain_pool():
        # Cleanup expired tokens
        STSTokenPool.query.filter(
            STSTokenPool.expiration <= datetime.now(timezone.utc)
        ).delete()
        
        # Generate new tokens if below minimum pool size
        current_count = STSTokenPool.query.count()
        while current_count < OSSService.MIN_POOL_SIZE:
            OSSService._generate_new_token()
            current_count += 1

    @staticmethod
    def generate_signed_url(user_id, filename):
        token = OSSService.get_available_token()
        auth = Auth(token.access_key_id, token.access_key_secret, token.security_token)
        bucket = Bucket(auth, current_app.config['OSS_ENDPOINT'], current_app.config['OSS_BUCKET_NAME'])
        
        # Format timestamp as YYYYMMDD_HHMMSS for better readability
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        file_extension = os.path.splitext(filename)[1] if '.' in filename else ''
        random_name = str(uuid.uuid4())
        
        object_name = f"user_upload/{user_id}/{timestamp}_{random_name}{file_extension}"
        return bucket.sign_url("PUT", object_name, current_app.config['OSS_TOKEN_DURATION']), object_name

        