# app/extensions.py
from flask_sqlalchemy import SQLAlchemy
from flask_jwt_extended import JWTManager
from flask_migrate import Migrate
from flask_socketio import SocketIO

db = SQLAlchemy()

jwt = JWTManager()

migrate = Migrate()

# Initialize SocketIO with environment-specific security
import os
from .config_security import SecurityConfig

# Configure SocketIO with proper security based on environment
socketio = SocketIO(
    cors_allowed_origins=SecurityConfig.get_socketio_origins(),
    async_mode='eventlet',
    engineio_logger=SecurityConfig.is_debug_mode(),
    socketio_logger=SecurityConfig.is_debug_mode()
)


# TODO: Add Flask-Limiter
# # Add Flask-Limiter to your extensions
# from flask_limiter import Limiter
# from flask_limiter.util import get_remote_address

# limiter = Limiter(
#     key_func=get_remote_address,
#     default_limits=["200 per day", "50 per hour"]
# )

