from flask import Flask
from .config import Config
from .extensions import db, jwt, migrate, socketio
from .routes import register_blueprints
from app.tasks.sts_pool import init_pool_maintenance


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    # Initialize extensions
    db.init_app(app)
    jwt.init_app(app)
    socketio.init_app(app)

    # Register blueprints (routes)
    register_blueprints(app)

    # Register SocketIO events
    from .sockets import register_socket_events
    register_socket_events(socketio)

    # # Create DB tables (for dev; in production use migrations)
    # with app.app_context():
    #     db.create_all()
    
    # Initialize in create_app()
    migrate.init_app(app, db)

    # limiter.init_app(app)
    
    # Initialize pool maintenance
    init_pool_maintenance(app)
    
    return app
