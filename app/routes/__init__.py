# app/routes/__init__.py
from flask import Blueprint

def register_blueprints(app):
    from app.routes import auth, user, post, comment, tag, reaction, file, course, analytics, search, gugu, oauth, notification, push, cache
    
    app.register_blueprint(auth.bp)
    app.register_blueprint(user.bp)
    app.register_blueprint(post.bp)
    app.register_blueprint(comment.bp)
    app.register_blueprint(tag.bp)
    app.register_blueprint(reaction.bp)
    app.register_blueprint(file.bp)
    app.register_blueprint(course.bp)
    app.register_blueprint(analytics.bp)
    app.register_blueprint(search.bp)
    app.register_blueprint(gugu.gugu_bp)
    app.register_blueprint(oauth.oauth_bp)
    app.register_blueprint(notification.bp)
    app.register_blueprint(push.bp)
    app.register_blueprint(cache.cache_bp)
