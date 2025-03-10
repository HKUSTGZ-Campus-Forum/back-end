# app/__init__.py
from flask import Flask
from .config import Config
from .extensions import db, jwt
from .routes import register_blueprints

def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    # Initialize extensions
    db.init_app(app)
    jwt.init_app(app)

    # Register blueprints (routes)
    register_blueprints(app)

    # Create DB tables (for dev; in production use migrations)
    with app.app_context():
        db.create_all()

    return app
