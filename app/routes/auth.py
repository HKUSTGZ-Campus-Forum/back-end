# app/routes/auth.py
from flask import Blueprint, current_app, request, jsonify
from app.models.user import User
from app.models.token import TokenBlacklist
from app.extensions import cache, db, jwt
from app.services.email_service import EmailService
from app.services.institutional_email import (
    acquire_email_transaction_lock,
    active_users_with_email,
    canonical_email_account,
    is_institutional_email,
    normalize_email,
)
import re
import secrets
from flask_jwt_extended import (
    create_access_token,
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

@bp.post('/register')
@bp.post('/login')
@bp.post('/forgot-password')
@bp.post('/reset-password')
@bp.post('/change-password')
def legacy_auth_disabled():
    """Reject password-based authentication after the campus SSO cutover."""
    response = jsonify({
        "code": "sso_only",
        "msg": "Password authentication is no longer available. Use HKUST(GZ) SSO.",
    })
    response.headers["Cache-Control"] = "no-store"
    return response, 410

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

def is_email(text):
    email_text = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(email_text, text) is not None

def is_hkust_email(email):
    """Backward-compatible HKUST-GZ email validation API."""
    return is_institutional_email(email)

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
    
    from app.services.campus_oidc import build_oidc_logout_url

    cookie_name = current_app.config.get(
        "CAMPUS_SSO_ID_TOKEN_COOKIE_NAME",
        "unikorn_oidc_id_token",
    )
    oidc_logout_url = build_oidc_logout_url(request.cookies.get(cookie_name, ""))
    response = jsonify({
        "msg": "Successfully logged out",
        "oidc_logout_url": oidc_logout_url,
    })
    response.delete_cookie(
        cookie_name,
        path=current_app.config.get("CAMPUS_SSO_COOKIE_PATH", "/api/auth"),
    )
    response.headers["Cache-Control"] = "no-store"
    return response, 200

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
