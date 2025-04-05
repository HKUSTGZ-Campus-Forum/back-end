from flask import Flask
from .config import Config
from .extensions import db, jwt, migrate
from .routes import register_blueprints


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    # Initialize extensions
    db.init_app(app)
    jwt.init_app(app)

    # Register blueprints (routes)
    register_blueprints(app)

    # # Create DB tables (for dev; in production use migrations)
    # with app.app_context():
    #     db.create_all()
    
    # Initialize in create_app()
    migrate.init_app(app, db)

    limiter.init_app(app)

    return app
