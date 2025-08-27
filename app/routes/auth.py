# app/routes/auth.py
from flask import Blueprint, request, jsonify
from app.models.user import User
from app.models.token import TokenBlacklist
from app.models.user_role import UserRole
from app.extensions import db, jwt
from app.services.email_service import EmailService
from app.services.content_moderation_service import content_moderation
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

@bp.route('/register', methods=['POST'])
def register():
    """Register a new user and send email verification"""
    data = request.get_json() or {}
    
    # Validate required fields
    if not data.get('username') or not data.get('password') or not data.get('email'):
        return jsonify({"msg": "Username, password, and email are required"}), 400
    
    username = data['username'].strip()
    email = data['email'].strip().lower()
    password = data['password']
    
    # Basic validation
    if len(username) < 3:
        return jsonify({"msg": "Username must be at least 3 characters"}), 400
    
    if len(password) < 6:
        return jsonify({"msg": "Password must be at least 6 characters"}), 400
    
    if not is_email(email):
        return jsonify({"msg": "Invalid email format"}), 400
    
    if not is_hkust_email(email):
        return jsonify({"msg": "Only HKUST-GZ email addresses are allowed (connect.hkust-gz.edu.cn or hkust-gz.edu.cn)"}), 400
    
    # Content moderation check for username
    moderation_result = content_moderation.moderate_text(
        content=username,
        data_id=f"username_register_{datetime.now().timestamp()}"
    )
    
    if not moderation_result['is_safe']:
        from flask import current_app
        current_app.logger.warning(f"Content moderation blocked username registration: {username} - {moderation_result['reason']}")
        return jsonify({
            "msg": "Username violates community guidelines and cannot be used",
            "details": moderation_result['reason'],
            "risk_level": moderation_result['risk_level']
        }), 400
    
    # Check if username already exists
    if User.query.filter_by(username=username, is_deleted=False).first():
        return jsonify({"msg": "Username already exists"}), 400
    
    # Check if email already exists
    if User.query.filter_by(email=email, is_deleted=False).first():
        return jsonify({"msg": "Email already registered"}), 400
    
    try:
        # Get default user role
        user_role = UserRole.query.filter_by(name=UserRole.USER).first()
        if not user_role:
            return jsonify({"msg": "System error: default role not found"}), 500
        
        # Create new user
        user = User(
            username=username,
            email=email,
            role_id=user_role.id,
            email_verified=False
        )
        user.set_password(password)
        
        # Generate and set verification code
        email_service = EmailService.from_app_config()
        verification_code = email_service.generate_verification_code()
        user.set_email_verification_code(verification_code)
        
        # Save user to database
        db.session.add(user)
        db.session.commit()
        
        # Send verification email
        result = email_service.send_verification_email(
            to_email=email,
            verification_code=verification_code,
            user_name=username
        )
        
        if not result.get('success'):
            # If email fails, still allow registration but notify user
            return jsonify({
                "msg": "User registered successfully, but verification email failed to send. Please try resending verification email.",
                "user_id": user.id,
                "email_sent": False,
                "email_error": result.get('error')
            }), 201
        
        return jsonify({
            "msg": "User registered successfully. Please check your email for verification code.",
            "user_id": user.id,
            "email_sent": True
        }), 201
        
    except Exception as e:
        db.session.rollback()
        return jsonify({"msg": f"Registration failed: {str(e)}"}), 500

@bp.route('/verify-email', methods=['POST'])
def verify_email():
    """Verify email with verification code"""
    data = request.get_json() or {}
    
    user_id = data.get('user_id')
    verification_code = data.get('verification_code')
    
    if not user_id or not verification_code:
        return jsonify({"msg": "User ID and verification code are required"}), 400
    
    user = User.query.get(user_id)
    if not user or user.is_deleted:
        return jsonify({"msg": "User not found"}), 404
    
    if user.email_verified:
        return jsonify({"msg": "Email already verified"}), 400
    
    if user.verify_email_code(verification_code):
        db.session.commit()
        return jsonify({"msg": "Email verified successfully"}), 200
    else:
        return jsonify({"msg": "Invalid or expired verification code"}), 400

@bp.route('/resend-verification', methods=['POST'])
def resend_verification():
    """Resend email verification code"""
    data = request.get_json() or {}
    
    user_id = data.get('user_id')
    if not user_id:
        return jsonify({"msg": "User ID is required"}), 400
    
    user = User.query.get(user_id)
    if not user or user.is_deleted:
        return jsonify({"msg": "User not found"}), 404
    
    if user.email_verified:
        return jsonify({"msg": "Email already verified"}), 400
    
    try:
        # Generate new verification code
        email_service = EmailService.from_app_config()
        verification_code = email_service.generate_verification_code()
        user.set_email_verification_code(verification_code)
        
        # Send verification email
        result = email_service.send_verification_email(
            to_email=user.email,
            verification_code=verification_code,
            user_name=user.username
        )
        
        db.session.commit()
        
        if result.get('success'):
            return jsonify({"msg": "Verification email sent successfully"}), 200
        else:
            return jsonify({
                "msg": "Failed to send verification email",
                "error": result.get('error')
            }), 500
            
    except Exception as e:
        db.session.rollback()
        return jsonify({"msg": f"Failed to resend verification: {str(e)}"}), 500

@bp.route('/forgot-password', methods=['POST'])
def forgot_password():
    """Send password reset email"""
    data = request.get_json() or {}
    
    email = data.get('email')
    if not email:
        return jsonify({"msg": "Email is required"}), 400
    
    email = email.strip().lower()
    if not is_email(email):
        return jsonify({"msg": "Invalid email format"}), 400
    
    if not is_hkust_email(email):
        return jsonify({"msg": "Only HKUST-GZ email addresses are allowed"}), 400
    
    user = User.query.filter_by(email=email, is_deleted=False).first()
    if not user:
        # Don't reveal if email exists for security
        return jsonify({"msg": "If the email exists, a password reset link has been sent"}), 200
    
    try:
        # Generate reset token
        email_service = EmailService.from_app_config()
        reset_token = email_service.generate_reset_token()
        user.set_password_reset_token(reset_token)
        
        # Send reset email
        result = email_service.send_password_reset_email(
            to_email=user.email,
            reset_token=reset_token,
            user_name=user.username
        )
        
        db.session.commit()
        
        return jsonify({"msg": "If the email exists, a password reset link has been sent"}), 200
        
    except Exception as e:
        db.session.rollback()
        return jsonify({"msg": "Failed to send reset email"}), 500

@bp.route('/reset-password', methods=['POST'])
def reset_password():
    """Reset password using reset token"""
    data = request.get_json() or {}
    
    token = data.get('token')
    new_password = data.get('password')
    
    if not token or not new_password:
        return jsonify({"msg": "Reset token and new password are required"}), 400
    
    if len(new_password) < 6:
        return jsonify({"msg": "Password must be at least 6 characters"}), 400
    
    user = User.query.filter_by(password_reset_token=token, is_deleted=False).first()
    if not user or not user.verify_password_reset_token(token):
        return jsonify({"msg": "Invalid or expired reset token"}), 400
    
    try:
        # Update password and clear reset token
        user.set_password(new_password)
        user.clear_password_reset_token()
        
        db.session.commit()
        
        return jsonify({"msg": "Password reset successfully"}), 200
        
    except Exception as e:
        db.session.rollback()
        return jsonify({"msg": f"Failed to reset password: {str(e)}"}), 500

@bp.route('/change-password', methods=['POST'])
@jwt_required()
def change_password():
    """Change password for authenticated user"""
    data = request.get_json() or {}
    
    current_password = data.get('current_password')
    new_password = data.get('new_password')
    
    if not current_password or not new_password:
        return jsonify({"msg": "Current password and new password are required"}), 400
    
    if len(new_password) < 6:
        return jsonify({"msg": "New password must be at least 6 characters"}), 400
    
    # Get current user
    current_user_id = get_jwt_identity()
    user = User.query.get(current_user_id)
    if not user or user.is_deleted:
        return jsonify({"msg": "User not found"}), 404
    
    # Verify current password
    if not user.check_password(current_password):
        return jsonify({"msg": "Current password is incorrect"}), 400
    
    # Check if new password is different from current
    if user.check_password(new_password):
        return jsonify({"msg": "New password must be different from current password"}), 400
    
    try:
        # Update password
        user.set_password(new_password)
        user.update_last_active()
        
        db.session.commit()
        
        return jsonify({"msg": "Password changed successfully"}), 200
        
    except Exception as e:
        db.session.rollback()
        return jsonify({"msg": f"Failed to change password: {str(e)}"}), 500

def is_email(text):
    email_text = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(email_text, text) is not None

def is_hkust_email(email):
    """Check if email belongs to HKUST-GZ domains"""
    if not email:
        return False
    
    email = email.lower().strip()
    
    # Basic email format check first
    if not is_email(email):
        return False
    
    allowed_domains = ['connect.hkust-gz.edu.cn', 'hkust-gz.edu.cn']
    
    for domain in allowed_domains:
        if email.endswith('@' + domain):
            # Additional check to ensure it's exactly the domain, not a subdomain
            email_parts = email.split('@')
            if len(email_parts) == 2 and email_parts[1] == domain:
                return True
    
    return False

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
