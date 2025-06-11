from flask import Blueprint, request, jsonify
from app.models.user import User
from app.models.user_role import UserRole as UserRoleModel
from app.models.post import Post
from app.models.comment import Comment
from app.models.reaction import Reaction
import re
from app.extensions import db
from flask_jwt_extended import jwt_required, get_jwt_identity
from datetime import datetime, timezone
from sqlalchemy.sql import func

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

# Add a try-except block to the create_user function
@bp.route('', methods=['POST'])
def create_user():
    try:
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
    except Exception as e:
        db.session.rollback()
        return jsonify({"msg": f"An error occurred: {str(e)}"}), 500

@bp.route('/<int:user_id>', methods=['GET'])
@jwt_required()
def get_user(user_id):
    user = User.query.get(user_id)
    if not user:
        return jsonify({"msg": "User not found"}), 404
    
    # Check if requesting own profile (include more details) or just public info
    current_user_id = get_jwt_identity()
    include_contact = (current_user_id == user_id)
    
    # Update last active time for the requesting user
    if str(current_user_id) == str(user_id):
        user.update_last_active()
    
    return jsonify(user.to_dict(include_contact=include_contact))

@bp.route('/<int:user_id>', methods=['PUT'])
@jwt_required()
def update_user(user_id):
    # Verify the requester is the same user or an admin
    current_user_id = get_jwt_identity()
    current_user = User.query.get(current_user_id)
    
    if not current_user or current_user.is_deleted:
        return jsonify({"msg": "Authenticated user not found or inactive"}), 401
    
    # Convert current_user_id to int for comparison since JWT identity is string
    if int(current_user_id) != user_id and not current_user.is_admin():
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
    
    # Convert current_user_id to int for comparison since JWT identity is string
    if int(current_user_id) != user_id and not current_user.is_admin():
        return jsonify({"msg": "Unauthorized to delete this user"}), 403
    
    user = User.query.get(user_id)
    if not user or user.is_deleted:
        return jsonify({"msg": "User not found"}), 404
    
    # Implement soft delete
    user.is_deleted = True
    user.deleted_at = datetime.now(timezone.utc)
    db.session.commit()
    
    return '', 204

@bp.route('/public/<int:user_id>', methods=['GET'])
def get_public_user_info(user_id):
    """Get public user information without authentication"""
    user = User.query.get(user_id)
    if not user or user.is_deleted:
        return jsonify({"msg": "User not found"}), 404
    
    # Return only public information
    return jsonify({
        "id": user.id,
        "username": user.username,
        "profile_picture_url": user.profile_picture_url,
        "role_name": user.get_role_name()
    })

# Add a new endpoint for user statistics
@bp.route('/<int:user_id>/stats', methods=['GET'])
def get_user_stats(user_id):
    """Get user statistics including post count, comment count, likes received, etc."""
    user = User.query.get(user_id)
    if not user or user.is_deleted:
        return jsonify({"msg": "User not found"}), 404
    
    # Get post count
    post_count = Post.query.filter_by(user_id=user_id, is_deleted=False).count()
    
    # Get comment count
    comment_count = Comment.query.filter_by(user_id=user_id, is_deleted=False).count()
    
    # Get total likes received (reactions on user's posts and comments)
    likes_received = db.session.query(func.count(Reaction.id)).join(
        Post, Reaction.post_id == Post.id
    ).filter(
        Post.user_id == user_id,
        Post.is_deleted == False
    ).scalar() or 0
    
    likes_received += db.session.query(func.count(Reaction.id)).join(
        Comment, Reaction.comment_id == Comment.id
    ).filter(
        Comment.user_id == user_id,
        Comment.is_deleted == False
    ).scalar() or 0
    
    # Get total view count (sum of views on user's posts)
    view_count = db.session.query(func.sum(Post.view_count)).filter(
        Post.user_id == user_id,
        Post.is_deleted == False
    ).scalar() or 0
    
    # Calculate total score (this is a simple example - adjust the formula as needed)
    total_score = (
        post_count * 10 +  # 10 points per post
        comment_count * 2 +  # 2 points per comment
        likes_received * 1 +  # 1 point per like
        view_count * 0.1  # 0.1 points per view
    )
    
    # Update last active time if the requesting user is authenticated and is the same as the target user
    try:
        current_user_id = get_jwt_identity()
        if current_user_id and str(current_user_id) == str(user_id):
            user.update_last_active()
    except RuntimeError:
        # Not authenticated, which is fine for this endpoint
        pass
    
    return jsonify({
        "post_count": post_count,
        "comment_count": comment_count,
        "likes_received": likes_received,
        "view_count": view_count,
        "total_score": round(total_score, 1)
    })
