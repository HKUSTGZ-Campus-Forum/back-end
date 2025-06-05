# app/routes/auth.py
from flask import Blueprint, request, jsonify
from app.models.user import User
from app.models.token import TokenBlacklist
from app.extensions import db, jwt
import re
from flask_jwt_extended import (
    create_access_token, 
    create_refresh_token,
    jwt_required, 
    get_jwt_identity, 
    get_jwt,
    current_user
)
from datetime import datetime, timezone

bp = Blueprint('auth', __name__, url_prefix='/auth')

# Setup the JWT loaders
@jwt.user_lookup_loader
def user_lookup_callback(_jwt_header, jwt_data):
    identity = jwt_data["sub"]
    user = User.query.get(identity)
    if user and user.is_deleted:
        return None  # Don't allow deleted users to authenticate
    return user

@jwt.token_in_blocklist_loader
def check_if_token_revoked(jwt_header, jwt_payload):
    jti = jwt_payload["jti"]
    return TokenBlacklist.is_token_revoked(jti)

# @bp.route('/register', methods=['POST'])
# def register():
#     data = request.get_json() or {}
#     if not data.get('username') or not data.get('password'):
#         return jsonify({"msg": "Username and password required"}), 400
    
#     # Check if username already exists
#     if User.query.filter_by(username=data['username']).first():
#         return jsonify({"msg": "Username already exists"}), 400

#     user = User(
#         username=data['username'], 
#         email=data.get('email', ''),
#         phone_number=data.get('phone_number', ''),
#         profile_picture_url=data.get('profile_picture_url', ''),
#         role=data.get('role_id', 'user')  # Default to regular user
#     )
#     user.set_password(data['password'])
#     db.session.add(user)
#     db.session.commit()

#     return jsonify({"msg": "User registered successfully"}), 201

def is_email(text):
    email_text = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(email_text, text) is not None

@bp.route('/login', methods=['POST'])
def login():
    data = request.get_json() or {}
    
    if not data.get('username') or not data.get('password'):
        return jsonify({"msg": "Username and password required"}), 400
    
    user_or_email = data.get('username')
    password = data.get('password')

    if is_email(user_or_email):
        user = User.query.filter_by(
            email=user_or_email, 
            is_deleted=False
        ).first()
    else:
        user = User.query.filter_by(
            username=user_or_email, 
            is_deleted=False
        ).first()

    if user is None or not user.check_password(data.get('password')):
        return jsonify({"msg": "Invalid username or password"}), 401

    # Update last active time
    user.update_last_active()

    # Create both access and refresh tokens
    access_token = create_access_token(identity=str(user.id))
    refresh_token = create_refresh_token(identity=str(user.id))
    
    # Return both tokens and user information
    return jsonify({
        "access_token": access_token,
        "refresh_token": refresh_token,
        "user": user.to_dict(include_contact=True)
    }), 200

@bp.route('/refresh', methods=['POST'])
@jwt_required(refresh=True)
def refresh():
    """Refresh access token using refresh token"""
    current_user_id = get_jwt_identity()
    
    # Verify the user still exists and is active
    user = User.query.get(current_user_id)
    if not user or user.is_deleted:
        return jsonify({"msg": "User not found or inactive"}), 401
    
    # Create a new access token
    access_token = create_access_token(identity=current_user_id)
    
    # Update last active time
    user.update_last_active()
    
    return jsonify({
        "access_token": access_token
    }), 200

@bp.route('/logout', methods=['POST'])
@jwt_required()
def logout():
    """Revoke tokens to implement logout"""
    jwt_data = get_jwt()
    jti = jwt_data["jti"]
    token_type = jwt_data["type"]
    user_id = get_jwt_identity()
    
    # Add token to blacklist
    expires = datetime.fromtimestamp(jwt_data["exp"], timezone.utc)
    token = TokenBlacklist(
        jti=jti,
        token_type=token_type,
        user_id=user_id,
        expires=expires
    )
    db.session.add(token)
    db.session.commit()
    
    return jsonify({"msg": "Successfully logged out"}), 200

# @bp.route('/logout-all', methods=['POST'])
# @jwt_required()
# def logout_all_devices():
#     """Revoke all tokens for a user (logout from all devices)"""
#     user_id = get_jwt_identity()
    
#     # Get all active refresh tokens for the user
#     # In a real-world scenario, you would store refresh tokens and revoke them all
#     # For this implementation, we'll just notify the user
    
#     return jsonify({"msg": "Successfully logged out from all devices"}), 200

# JWT token error handlers
@jwt.expired_token_loader
def expired_token_callback(jwt_header, jwt_payload):
    return jsonify({
        "msg": "Token has expired",
        "error": "token_expired"
    }), 401

@jwt.invalid_token_loader
def invalid_token_callback(error):
    return jsonify({
        "msg": "Signature verification failed",
        "error": "invalid_token"
    }), 401

@jwt.unauthorized_loader
def missing_token_callback(error):
    return jsonify({
        "msg": "Authorization header is missing",
        "error": "authorization_required"
    }), 401

@jwt.revoked_token_loader
def revoked_token_callback(jwt_header, jwt_payload):
    return jsonify({
        "msg": "Token has been revoked",
        "error": "token_revoked"
    }), 401
