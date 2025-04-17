# app/config.py
import os
from datetime import timedelta
from dotenv import load_dotenv

# Load .env from the root project directory
basedir = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(basedir, '..', '.env'))

class Config:
    SECRET_KEY = os.getenv('SECRET_KEY', 'your_default_secret_key')
    SQLALCHEMY_DATABASE_URI = os.getenv('DATABASE_URL', 'postgres:///app.db')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    
    # JWT Configuration
    JWT_SECRET_KEY = os.getenv('JWT_SECRET_KEY', 'your_jwt_secret_key')
    JWT_ACCESS_TOKEN_EXPIRES = timedelta(minutes=15)  # Short-lived access tokens
    JWT_REFRESH_TOKEN_EXPIRES = timedelta(days=60)    # Longer-lived refresh tokens
    JWT_BLACKLIST_ENABLED = True
    JWT_BLACKLIST_TOKEN_CHECKS = ['access', 'refresh']
    
    # OSS Configuration
    OSS_ROLE_ARN = os.getenv('OSS_ROLE_ARN')
    OSS_BUCKET_NAME = os.getenv('OSS_BUCKET_NAME')
    OSS_ENDPOINT = os.getenv('OSS_ENDPOINT')
    OSS_REGION_ID = os.getenv('OSS_REGION_ID', 'cn-hangzhou')
    OSS_TOKEN_DURATION = int(os.getenv('OSS_TOKEN_DURATION', 3600))
