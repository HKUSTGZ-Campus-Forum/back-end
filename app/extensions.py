# app/extensions.py
from flask_sqlalchemy import SQLAlchemy
from flask_jwt_extended import JWTManager
from flask_migrate import Migrate
from flask_socketio import SocketIO

db = SQLAlchemy()

jwt = JWTManager()

migrate = Migrate()

# Initialize SocketIO with CORS support
socketio = SocketIO(cors_allowed_origins="*", async_mode='threading')


# TODO: Add Flask-Limiter
# # Add Flask-Limiter to your extensions
# from flask_limiter import Limiter
# from flask_limiter.util import get_remote_address

# limiter = Limiter(
#     key_func=get_remote_address,
#     default_limits=["200 per day", "50 per hour"]
# )

