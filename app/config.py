# app/config.py
import os

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'your_default_secret_key')
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL', 'sqlite:///app.db')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    JWT_SECRET_KEY = os.environ.get('JWT_SECRET_KEY', 'your_jwt_secret_key')
    # Add OSS and other Alibaba Cloud configuration here, e.g.:
    # OSS_ACCESS_KEY = os.environ.get('OSS_ACCESS_KEY')
    # OSS_SECRET_KEY = os.environ.get('OSS_SECRET_KEY')
    # OSS_ENDPOINT = os.environ.get('OSS_ENDPOINT')
    # OSS_BUCKET_NAME = os.environ.get('OSS_BUCKET_NAME')
