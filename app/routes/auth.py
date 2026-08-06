# app/routes/auth.py
from flask import Blueprint, current_app, request, jsonify
from app.models.user import User
from app.models.token import TokenBlacklist
from app.models.user_role import UserRole
from app.extensions import cache, db, jwt
from app.services.email_service import EmailService
from app.services.content_moderation_service import content_moderation
from app.services.institutional_email import (
    acquire_email_transaction_lock,
    active_email_owner,
    active_users_with_email,
    canonical_email_account,
    is_institutional_email,
    normalize_email,
)
import re
import secrets
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

EMAIL_VERIFICATION_FAILURE_LIMIT = 5
EMAIL_VERIFICATION_FAILURE_WINDOW_SECONDS = 15 * 60
EMAIL_VERIFICATION_RESEND_COOLDOWN_SECONDS = 60


def _verification_attempt_key(user_id):
    return f"email-verification:failures:{user_id}"


def _verification_resend_key(user_id):
    return f"email-verification:resend:{user_id}"


def _verification_failure_count(user_id):
    """Read the failure counter, failing open if the cache is unavailable."""
    try:
        return int(cache.get(_verification_attempt_key(user_id)) or 0)
    except Exception as exc:
        current_app.logger.warning(
            "Email verification attempt cache unavailable for user %s: %s",
            user_id,
            exc,
        )
        return 0


def _record_verification_failure(user_id):
    """Increment the bounded failure counter without making cache a dependency."""
    key = _verification_attempt_key(user_id)
    try:
        if cache.add(key, 1, timeout=EMAIL_VERIFICATION_FAILURE_WINDOW_SECONDS):
            return 1
        backend_increment = getattr(cache.cache, "inc", None)
        if callable(backend_increment):
            return int(backend_increment(key))

        # Some custom Flask-Caching backends expose only the portable facade.
        # This branch is not atomic, but still preserves throttling rather than
        # disabling it; Redis and SimpleCache both use the atomic branch above.
        failures = int(cache.get(key) or 0) + 1
        cache.set(key, failures, timeout=EMAIL_VERIFICATION_FAILURE_WINDOW_SECONDS)
        return failures
    except Exception as exc:
        current_app.logger.warning(
            "Could not record email verification failure for user %s: %s",
            user_id,
            exc,
        )
        return None


def _clear_verification_failures(user_id):
    try:
        cache.delete(_verification_attempt_key(user_id))
    except Exception as exc:
        current_app.logger.warning(
            "Could not clear email verification failures for user %s: %s",
            user_id,
            exc,
        )


def _claim_resend_window(user_id):
    """Atomically claim a resend slot where the cache backend supports add()."""
    try:
        return bool(cache.add(
            _verification_resend_key(user_id),
            1,
            timeout=EMAIL_VERIFICATION_RESEND_COOLDOWN_SECONDS,
        ))
    except Exception as exc:
        current_app.logger.warning(
            "Email verification resend cache unavailable for user %s: %s",
            user_id,
            exc,
        )
        return True


def _verification_code_matches(user, submitted_code):
    expires_at = user.email_verification_expires_at
    if not user.email_verification_code or not expires_at:
        return False
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) > expires_at:
        return False
    return secrets.compare_digest(
        str(user.email_verification_code),
        str(submitted_code),
    )

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
    email = normalize_email(data['email'])
    password = data['password']
    
    # Basic validation
    if len(username) < 3:
        return jsonify({"msg": "Username must be at least 3 characters"}), 400
    
    if len(password) < 6:
        return jsonify({"msg": "Password must be at least 6 characters"}), 400
    
    if not is_email(email):
        return jsonify({"msg": "Invalid email format"}), 400
    
    if not is_institutional_email(email):
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
    
    try:
        acquire_email_transaction_lock(email)

        # Email identity is case-insensitive throughout the application. The
        # transaction lock prevents two PostgreSQL requests passing this check
        # concurrently before either account is committed.
        if active_email_owner(email):
            db.session.rollback()
            return jsonify({"msg": "Email already registered"}), 400

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

    normalized_email = normalize_email(user.email)
    if not is_institutional_email(normalized_email):
        return jsonify({"msg": "Only HKUST-GZ email addresses can be verified"}), 400

    if _verification_failure_count(user.id) >= EMAIL_VERIFICATION_FAILURE_LIMIT:
        response = jsonify({"msg": "Too many failed verification attempts. Please try again later."})
        response.headers["Retry-After"] = str(EMAIL_VERIFICATION_FAILURE_WINDOW_SECONDS)
        return response, 429

    try:
        acquire_email_transaction_lock(normalized_email)

        # Re-query after taking the per-address lock so the ownership decision
        # observes any account committed by a concurrent request.
        user = User.query.get(user_id)
        canonical_account = canonical_email_account(normalized_email)
        other_verified_account = active_users_with_email(normalized_email).filter(
            User.id != user.id,
            User.email_verified.is_(True),
        ).first()

        if not canonical_account or canonical_account.id != user.id or other_verified_account:
            db.session.rollback()
            return jsonify({
                "msg": "This email address belongs to another active account"
            }), 409

        if not _verification_code_matches(user, verification_code):
            db.session.rollback()
            _record_verification_failure(user.id)
            return jsonify({"msg": "Invalid or expired verification code"}), 400

        user.email = normalized_email
        user.email_verified = True
        user.email_verification_code = None
        user.email_verification_expires_at = None
        db.session.commit()
        _clear_verification_failures(user.id)
        return jsonify({"msg": "Email verified successfully"}), 200
    except Exception as exc:
        db.session.rollback()
        current_app.logger.exception(
            "Email verification failed for user %s: %s",
            user_id,
            exc,
        )
        return jsonify({"msg": "Email verification failed"}), 500

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

    normalized_email = normalize_email(user.email)
    if not is_institutional_email(normalized_email):
        return jsonify({"msg": "Only HKUST-GZ email addresses can be verified"}), 400

    if not _claim_resend_window(user.id):
        response = jsonify({"msg": "Please wait before requesting another verification email"})
        response.headers["Retry-After"] = str(EMAIL_VERIFICATION_RESEND_COOLDOWN_SECONDS)
        return response, 429
    
    try:
        acquire_email_transaction_lock(normalized_email)
        canonical_account = canonical_email_account(normalized_email)
        other_verified_account = active_users_with_email(normalized_email).filter(
            User.id != user.id,
            User.email_verified.is_(True),
        ).first()
        if not canonical_account or canonical_account.id != user.id or other_verified_account:
            db.session.rollback()
            return jsonify({
                "msg": "This email address belongs to another active account"
            }), 409

        # Generate new verification code
        email_service = EmailService.from_app_config()
        verification_code = email_service.generate_verification_code()
        user.email = normalized_email
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
    
    email = normalize_email(email)
    if not is_email(email):
        return jsonify({"msg": "Invalid email format"}), 400
    
    if not is_hkust_email(email):
        return jsonify({"msg": "Only HKUST-GZ email addresses are allowed"}), 400
    
    user = active_email_owner(email)
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
    """Backward-compatible HKUST-GZ email validation API."""
    return is_institutional_email(email)

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
