# app/config.py
import os
import re
from datetime import timedelta
from dotenv import load_dotenv
from sqlalchemy.engine import make_url

# Load .env from the root project directory
basedir = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(basedir, '..', '.env'))


def normalize_database_config(database_url):
    """Return a SQLAlchemy URI and engine options for DATABASE_URL."""
    if database_url.startswith('postgres://'):
        database_url = 'postgresql://' + database_url[len('postgres://'):]

    url = make_url(database_url)
    query = dict(url.query)
    schema = query.pop('schema', None)
    engine_options = {}

    if schema is not None:
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*(,[A-Za-z_][A-Za-z0-9_]*)*', schema):
            raise ValueError('invalid database schema name')
        url = url.set(query=query)
        if url.get_backend_name() == 'postgresql':
            engine_options['connect_args'] = {'options': f'-csearch_path={schema}'}

    return url.render_as_string(hide_password=False), engine_options


_database_uri, _database_engine_options = normalize_database_config(
    os.getenv('DATABASE_URL', 'postgres:///app.db')
)


class Config:
    SECRET_KEY = os.getenv('SECRET_KEY', 'your_default_secret_key')
    SQLALCHEMY_DATABASE_URI = _database_uri
    SQLALCHEMY_ENGINE_OPTIONS = _database_engine_options
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    
    # JWT Configuration
    JWT_SECRET_KEY = os.getenv('JWT_SECRET_KEY', 'your_jwt_secret_key')
    JWT_ACCESS_TOKEN_EXPIRES = timedelta(minutes=15)  # Short-lived access tokens
    JWT_REFRESH_TOKEN_EXPIRES = timedelta(days=60)    # Longer-lived refresh tokens
    JWT_BLACKLIST_ENABLED = True
    JWT_BLACKLIST_TOKEN_CHECKS = ['access', 'refresh']
    
    # Base Alibaba Cloud Credentials (Needed for STS client)
    ALIBABA_CLOUD_ACCESS_KEY_ID = os.getenv('ALIBABA_CLOUD_ACCESS_KEY_ID')
    ALIBABA_CLOUD_ACCESS_KEY_SECRET = os.getenv('ALIBABA_CLOUD_ACCESS_KEY_SECRET')
    
    # OSS Configuration
    OSS_ROLE_ARN = os.getenv('OSS_ROLE_ARN')
    OSS_BUCKET_NAME = os.getenv('OSS_BUCKET_NAME')
    OSS_ENDPOINT = os.getenv('OSS_ENDPOINT')
    OSS_REGION_ID = os.getenv('OSS_REGION_ID', 'cn-hangzhou')
    OSS_TOKEN_DURATION = int(os.getenv('OSS_TOKEN_DURATION', 3600))
    OSS_PUBLIC_URL = os.getenv('OSS_PUBLIC_URL') or (
        f'https://{os.getenv("OSS_BUCKET_NAME")}.{os.getenv("OSS_ENDPOINT", "").replace("http://", "").replace("https://", "")}'
        if os.getenv("OSS_BUCKET_NAME") and os.getenv("OSS_ENDPOINT") else None
    )
    
    # DirectMail Configuration (uses same Alibaba Cloud credentials)
    ALIBABA_DM_REGION = os.getenv('ALIBABA_DM_REGION', 'ap-southeast-1')
    ALIBABA_DM_ACCOUNT_NAME = os.getenv('ALIBABA_DM_ACCOUNT_NAME', 'no-reply@unikorn.axfff.com')
    ALIBABA_DM_FROM_ALIAS = os.getenv('ALIBABA_DM_FROM_ALIAS', 'uniKorn 校园论坛')
    ALIBABA_CLOUD_EMAIL_SMTP_SECRET = os.getenv('ALIBABA_CLOUD_EMAIL_SMTP_SECRET')
    
    # Email Verification Settings
    EMAIL_VERIFICATION_EXPIRES_MINUTES = int(os.getenv('EMAIL_VERIFICATION_EXPIRES_MINUTES', '10'))
    PASSWORD_RESET_EXPIRES_HOURS = int(os.getenv('PASSWORD_RESET_EXPIRES_HOURS', '1'))
    
    # Frontend URLs for email templates
    FRONTEND_BASE_URL = os.getenv('FRONTEND_BASE_URL', 'https://unikorn.axfff.com')
    
    # Redis Configuration for Caching
    REDIS_URL = os.getenv('REDIS_URL', 'redis://localhost:6379/0')
    
    # Cache Configuration
    CACHE_TYPE = 'redis'
    CACHE_REDIS_URL = REDIS_URL
    CACHE_DEFAULT_TIMEOUT = 2700  # 45 minutes (45*60 seconds)
    
    # File URL Cache Settings
    FILE_URL_CACHE_TIMEOUT = 2700  # 45 minutes - shorter than 1hr URL expiry
    FILE_URL_CACHE_KEY_PREFIX = 'file_url:'

    # AI/ML Service Configuration
    DASHSCOPE_API_KEY = os.getenv('DASHSCOPE_API_KEY')
    DASHVECTOR_API_KEY = os.getenv('DASHVECTOR_API_KEY')
    DASHVECTOR_ENDPOINT = os.getenv('DASHVECTOR_ENDPOINT')

    # Background Tasks Configuration
    ENABLE_BACKGROUND_TASKS = os.getenv('ENABLE_BACKGROUND_TASKS', 'true').lower() == 'true'
    EMBEDDING_MAINTENANCE_INTERVAL_MINUTES = int(os.getenv('EMBEDDING_MAINTENANCE_INTERVAL_MINUTES', '60'))  # 1 hour default
    EMBEDDING_MAINTENANCE_BATCH_SIZE = int(os.getenv('EMBEDDING_MAINTENANCE_BATCH_SIZE', '50'))
    EMBEDDING_MAINTENANCE_MAX_TIME_MINUTES = int(os.getenv('EMBEDDING_MAINTENANCE_MAX_TIME_MINUTES', '30'))
