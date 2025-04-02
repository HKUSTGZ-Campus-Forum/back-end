from flask import Blueprint, request, jsonify
from app.models.user import User
from app.models.user_role import UserRole as UserRoleModel
import re
from app.extensions import db
from flask_jwt_extended import jwt_required, get_jwt_identity
from datetime import datetime, timezone

bp = Blueprint('user', __name__, url_prefix='/users')

# Validation patterns
USERNAME_PATTERN = re.compile(r'^[a-zA-Z0-9_]{3,50}$')
EMAIL_PATTERN = re.compile(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$')
PHONE_PATTERN = re.compile(r'^\+?[0-9]{10,15}$')

def validate_username(username):
    """Validate username format"""
    if not username or not USERNAME_PATTERN.match(username):
        return False, "Username must be 3-50 characters and contain only letters, numbers, and underscores"
    return True, ""

def validate_email(email):
    """Validate email format"""
    if email and not EMAIL_PATTERN.match(email):
        return False, "Invalid email format"
    return True, ""

def validate_phone(phone):
    """Validate phone number format"""
    if phone and not PHONE_PATTERN.match(phone):
        return False, "Phone number must be 10-15 digits, optionally with a + prefix"
    return True, ""

@bp.route('', methods=['POST'])
def create_user():
    data = request.get_json() or {}
    
    # Required fields
    if not data.get('username'):
        return jsonify({"msg": "Username is required"}), 400
    if not data.get('password'):  # Changed from password_hash
        return jsonify({"msg": "Password is required"}), 400
    
    # Validate username format
    is_valid, error_msg = validate_username(data['username'])
    if not is_valid:
        return jsonify({"msg": error_msg}), 400
    
    # Validate email format if provided
    is_valid, error_msg = validate_email(data.get('email', ''))
    if not is_valid:
        return jsonify({"msg": error_msg}), 400
    
    # Validate phone format if provided
    is_valid, error_msg = validate_phone(data.get('phone_number', ''))
    if not is_valid:
        return jsonify({"msg": error_msg}), 400
    
    # Check if username already exists
    if User.query.filter_by(username=data['username'], is_deleted=False).first():
        return jsonify({"msg": "Username already exists"}), 400
    
    # Get default role_id (regular user)
    default_role = UserRoleModel.query.filter_by(name=UserRoleModel.USER).first()
    if not default_role:
        return jsonify({"msg": "Default user role not found in database"}), 500
    
    # Create new user
    user = User(
        username=data['username'],
        email=data.get('email', ''),
        phone_number=data.get('phone_number', ''),
        profile_picture_url=data.get('profile_picture_url', ''),
        role_id=data.get('role_id', default_role.id)  # Use role_id from role table
    )
    user.set_password(data['password'])  # Changed from password_hash
    
    db.session.add(user)
    db.session.commit()
    
    return jsonify(user.to_dict(include_contact=True)), 201

@bp.route('/<int:user_id>', methods=['GET'])
@jwt_required()
def get_user(user_id):
    user = User.query.get(user_id)
    if not user:
        return jsonify({"msg": "User not found"}), 404
    
    # Check if requesting own profile (include more details) or just public info
    current_user_id = get_jwt_identity()
    include_contact = (current_user_id == user_id)
    
    return jsonify(user.to_dict(include_contact=include_contact))

@bp.route('/<int:user_id>', methods=['PUT'])
@jwt_required()
def update_user(user_id):
    # Verify the requester is the same user or an admin
    current_user_id = get_jwt_identity()
    current_user = User.query.get(current_user_id)
    
    if not current_user or current_user.is_deleted:
        return jsonify({"msg": "Authenticated user not found or inactive"}), 401
    
    if current_user_id != user_id and not current_user.is_admin():
        return jsonify({"msg": "Unauthorized to modify this user"}), 403
    
    user = User.query.get(user_id)
    if not user or user.is_deleted:
        return jsonify({"msg": "User not found"}), 404
    
    data = request.get_json() or {}
    
    # Update fields if provided
    if 'username' in data and data['username'] != user.username:
        # Validate username format
        is_valid, error_msg = validate_username(data['username'])
        if not is_valid:
            return jsonify({"msg": error_msg}), 400
            
        # Check if username already exists
        if User.query.filter_by(username=data['username'], is_deleted=False).first():
            return jsonify({"msg": "Username already exists"}), 400
        user.username = data['username']
        
    if 'email' in data:
        # Validate email format
        is_valid, error_msg = validate_email(data['email'])
        if not is_valid:
            return jsonify({"msg": error_msg}), 400
            
        user.email = data['email']
        # Reset email verification if email changed
        if data['email'] != user.email:
            user.email_verified = False
            
    if 'phone_number' in data:
        # Validate phone format
        is_valid, error_msg = validate_phone(data['phone_number'])
        if not is_valid:
            return jsonify({"msg": error_msg}), 400
            
        user.phone_number = data['phone_number']
        # Reset phone verification if number changed
        if data['phone_number'] != user.phone_number:
            user.phone_verified = False
            
    if 'profile_picture_url' in data:
        user.profile_picture_url = data['profile_picture_url']
    
    # Only allow role changes if requester is admin
    if 'role_id' in data and current_user.is_admin():
        user.role_id = data['role_id']
    
    # If password provided, update it
    if 'password' in data:  # Changed from password_hash
        user.set_password(data['password'])
    
    db.session.commit()
    return jsonify(user.to_dict(include_contact=True))

@bp.route('/<int:user_id>', methods=['DELETE'])
@jwt_required()
def delete_user(user_id):
    # Verify the requester is the same user or an admin
    current_user_id = get_jwt_identity()
    current_user = User.query.get(current_user_id)
    
    if not current_user or current_user.is_deleted:
        return jsonify({"msg": "Authenticated user not found or inactive"}), 401
    
    if current_user_id != user_id and not current_user.is_admin():
        return jsonify({"msg": "Unauthorized to delete this user"}), 403
    
    user = User.query.get(user_id)
    if not user or user.is_deleted:
        return jsonify({"msg": "User not found"}), 404
    
    # Implement soft delete
    user.is_deleted = True
    user.deleted_at = datetime.now(timezone.utc)
    db.session.commit()
    
    return '', 204
