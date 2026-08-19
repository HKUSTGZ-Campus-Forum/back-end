# app/extensions.py
from flask_sqlalchemy import SQLAlchemy
from flask_jwt_extended import JWTManager
from flask_migrate import Migrate
from flask_caching import Cache
from authlib.integrations.flask_client import OAuth

db = SQLAlchemy()

jwt = JWTManager()

migrate = Migrate()

# Initialize cache
cache = Cache()

# OpenID Connect client registry. Providers are registered from application
# configuration during app creation so tests and deployments can supply their
# own issuer and credentials without module-level network access.
oauth = OAuth()


# TODO: Add Flask-Limiter
# # Add Flask-Limiter to your extensions
# from flask_limiter import Limiter
# from flask_limiter.util import get_remote_address

# limiter = Limiter(
#     key_func=get_remote_address,
#     default_limits=["200 per day", "50 per hour"]
# )
