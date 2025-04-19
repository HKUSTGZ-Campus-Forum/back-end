# app/routes/__init__.py
from flask import Blueprint

def register_blueprints(app):
    from app.routes import auth, user, post, comment, tag, reaction
    
    app.register_blueprint(auth.bp)
    app.register_blueprint(user.bp)
    app.register_blueprint(post.bp)
    app.register_blueprint(comment.bp)
    app.register_blueprint(tag.bp)
    app.register_blueprint(reaction.bp)
    app.register_blueprint(file.bp)
