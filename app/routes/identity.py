from flask import Blueprint, request, jsonify, current_app
from flask_jwt_extended import jwt_required, get_jwt_identity
from sqlalchemy.exc import IntegrityError
from app.extensions import db
from app.models.identity_type import IdentityType
from app.models.user_identity import UserIdentity
from app.models.user import User
from app.models.file import File
from app.models.post import Post
from app.models.comment import Comment
from app.models.gugu_message import GuguMessage
import json

identity_bp = Blueprint('identity', __name__, url_prefix='/identities')

@identity_bp.route('/types', methods=['GET'])
def get_identity_types():
    """Get all active identity types"""
    try:
        identity_types = IdentityType.get_active_types()
        return jsonify({
            "success": True,
            "identity_types": [identity_type.to_dict() for identity_type in identity_types]
        }), 200
    except Exception as e:
        current_app.logger.error(f"Error fetching identity types: {e}")
        return jsonify({"success": False, "error": "Failed to fetch identity types"}), 500

@identity_bp.route('/request', methods=['POST'])
@jwt_required()
def request_verification():
    """Request identity verification"""
    try:
        current_user_id = get_jwt_identity()
        data = request.get_json()
        
        if not data:
            return jsonify({"success": False, "error": "No data provided"}), 400
        
        identity_type_id = data.get('identity_type_id')
        verification_documents = data.get('verification_documents', [])
        notes = data.get('notes', '')
        
        if not identity_type_id:
            return jsonify({"success": False, "error": "Identity type ID is required"}), 400
        
        # Check if identity type exists and is active
        identity_type = IdentityType.query.filter_by(id=identity_type_id, is_active=True).first()
        if not identity_type:
            return jsonify({"success": False, "error": "Invalid identity type"}), 400
        
        # Check if user already has a verification request for this identity type
        existing_verification = UserIdentity.query.filter_by(
            user_id=current_user_id,
            identity_type_id=identity_type_id
        ).first()
        
        if existing_verification:
            if existing_verification.status == UserIdentity.PENDING:
                return jsonify({"success": False, "error": "You already have a pending verification for this identity type"}), 400
            elif existing_verification.status == UserIdentity.APPROVED:
                return jsonify({"success": False, "error": "You are already verified for this identity type"}), 400
        
        # Validate file IDs if provided
        validated_documents = []
        if verification_documents:
            for file_id in verification_documents:
                file_record = File.query.filter_by(
                    id=file_id,
                    user_id=current_user_id,
                    is_deleted=False
                ).first()
                if file_record:
                    validated_documents.append({
                        "file_id": file_id,
                        "filename": file_record.filename,
                        "uploaded_at": file_record.created_at.isoformat()
                    })
        
        # Create new verification request (or update existing rejected one)
        if existing_verification and existing_verification.status in [UserIdentity.REJECTED, UserIdentity.REVOKED]:
            # Update existing verification
            user_verification = existing_verification
            user_verification.status = UserIdentity.PENDING
            user_verification.verification_documents = validated_documents if validated_documents else None
            user_verification.notes = notes
            user_verification.rejection_reason = None
            user_verification.verified_by = None
            user_verification.verified_at = None
        else:
            # Create new verification request
            user_verification = UserIdentity(
                user_id=current_user_id,
                identity_type_id=identity_type_id,
                verification_documents=validated_documents if validated_documents else None,
                notes=notes
            )
            db.session.add(user_verification)
        
        db.session.commit()
        
        return jsonify({
            "success": True,
            "message": "Identity verification request submitted successfully",
            "verification": user_verification.to_dict(include_documents=True)
        }), 201
        
    except IntegrityError:
        db.session.rollback()
        return jsonify({"success": False, "error": "Database constraint violation"}), 400
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error submitting identity verification request: {e}")
        return jsonify({"success": False, "error": "Failed to submit verification request"}), 500

@identity_bp.route('/my-requests', methods=['GET'])
@jwt_required()
def get_my_verification_requests():
    """Get current user's verification requests"""
    try:
        current_user_id = get_jwt_identity()
        
        verifications = UserIdentity.query.filter_by(user_id=current_user_id).all()
        
        return jsonify({
            "success": True,
            "verifications": [verification.to_dict(include_documents=True) for verification in verifications]
        }), 200
        
    except Exception as e:
        current_app.logger.error(f"Error fetching user verification requests: {e}")
        return jsonify({"success": False, "error": "Failed to fetch verification requests"}), 500

@identity_bp.route('/my-identities', methods=['GET'])
@jwt_required()
def get_my_identities():
    """Get current user's all identity records (all statuses)"""
    try:
        current_user_id = get_jwt_identity()
        
        # Get all identity records for the user
        identities = UserIdentity.query.filter_by(user_id=current_user_id).all()
        
        return jsonify({
            "success": True,
            "identities": [identity.to_dict() for identity in identities]
        }), 200
        
    except Exception as e:
        current_app.logger.error(f"Error fetching user identities: {e}")
        return jsonify({"success": False, "error": "Failed to fetch user identities"}), 500

@identity_bp.route('/my-verified', methods=['GET'])
@jwt_required()
def get_my_verified_identities():
    """Get current user's verified identities (active only)"""
    try:
        current_user_id = get_jwt_identity()
        
        verified_identities = UserIdentity.get_user_active_identities(current_user_id)
        
        return jsonify({
            "success": True,
            "verified_identities": [identity.to_dict() for identity in verified_identities]
        }), 200
        
    except Exception as e:
        current_app.logger.error(f"Error fetching user verified identities: {e}")
        return jsonify({"success": False, "error": "Failed to fetch verified identities"}), 500

@identity_bp.route('/<int:verification_id>/update', methods=['PUT'])
@jwt_required()
def update_verification_request(verification_id):
    """Update an existing verification request (only if pending)"""
    try:
        current_user_id = get_jwt_identity()
        data = request.get_json()
        
        if not data:
            return jsonify({"success": False, "error": "No data provided"}), 400
        
        # Find the verification request
        verification = UserIdentity.query.filter_by(
            id=verification_id,
            user_id=current_user_id
        ).first()
        
        if not verification:
            return jsonify({"success": False, "error": "Verification request not found"}), 404
        
        if verification.status != UserIdentity.PENDING:
            return jsonify({"success": False, "error": "Can only update pending verification requests"}), 400
        
        # Update allowed fields
        verification_documents = data.get('verification_documents')
        notes = data.get('notes')
        
        if verification_documents is not None:
            # Validate file IDs if provided
            validated_documents = []
            if verification_documents:
                for file_id in verification_documents:
                    file_record = File.query.filter_by(
                        id=file_id,
                        user_id=current_user_id,
                        is_deleted=False
                    ).first()
                    if file_record:
                        validated_documents.append({
                            "file_id": file_id,
                            "filename": file_record.filename,
                            "uploaded_at": file_record.created_at.isoformat()
                        })
            
            verification.verification_documents = validated_documents if validated_documents else None
        
        if notes is not None:
            verification.notes = notes
        
        db.session.commit()
        
        return jsonify({
            "success": True,
            "message": "Verification request updated successfully",
            "verification": verification.to_dict(include_documents=True)
        }), 200
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error updating verification request: {e}")
        return jsonify({"success": False, "error": "Failed to update verification request"}), 500

# Admin endpoints for identity verification management
@identity_bp.route('/admin/pending', methods=['GET'])
@jwt_required()
def get_pending_verifications():
    """Get all pending verification requests (admin only)"""
    try:
        current_user_id = get_jwt_identity()
        current_user = User.query.get(current_user_id)
        
        if not current_user or not current_user.is_admin():
            return jsonify({"success": False, "error": "Access denied"}), 403
        
        pending_verifications = UserIdentity.get_pending_verifications()
        
        result = []
        for verification in pending_verifications:
            verification_data = verification.to_dict(include_documents=True, include_admin_info=True)
            # Add user information
            if verification.user:
                verification_data["user"] = {
                    "id": verification.user.id,
                    "username": verification.user.username,
                    "email": verification.user.email,
                    "created_at": verification.user.created_at.isoformat()
                }
            result.append(verification_data)
        
        return jsonify({
            "success": True,
            "pending_verifications": result
        }), 200
        
    except Exception as e:
        current_app.logger.error(f"Error fetching pending verifications: {e}")
        return jsonify({"success": False, "error": "Failed to fetch pending verifications"}), 500

@identity_bp.route('/admin/<int:verification_id>/approve', methods=['POST'])
@jwt_required()
def approve_verification(verification_id):
    """Approve a verification request (admin only)"""
    try:
        current_user_id = get_jwt_identity()
        current_user = User.query.get(current_user_id)
        
        if not current_user or not current_user.is_admin():
            return jsonify({"success": False, "error": "Access denied"}), 403
        
        data = request.get_json() or {}
        notes = data.get('notes', '')
        expires_days = data.get('expires_days')  # Optional expiration
        
        verification = UserIdentity.query.get(verification_id)
        if not verification:
            return jsonify({"success": False, "error": "Verification request not found"}), 404
        
        if verification.status != UserIdentity.PENDING:
            return jsonify({"success": False, "error": "Can only approve pending verification requests"}), 400
        
        # Set expiration if provided
        if expires_days:
            from datetime import datetime, timezone, timedelta
            verification.expires_at = datetime.now(timezone.utc) + timedelta(days=expires_days)
        
        verification.approve(current_user_id, notes)
        db.session.commit()
        
        return jsonify({
            "success": True,
            "message": "Verification request approved successfully",
            "verification": verification.to_dict(include_admin_info=True)
        }), 200
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error approving verification: {e}")
        return jsonify({"success": False, "error": "Failed to approve verification"}), 500

@identity_bp.route('/admin/<int:verification_id>/reject', methods=['POST'])
@jwt_required()
def reject_verification(verification_id):
    """Reject a verification request (admin only)"""
    try:
        current_user_id = get_jwt_identity()
        current_user = User.query.get(current_user_id)
        
        if not current_user or not current_user.is_admin():
            return jsonify({"success": False, "error": "Access denied"}), 403
        
        data = request.get_json()
        if not data or not data.get('reason'):
            return jsonify({"success": False, "error": "Rejection reason is required"}), 400
        
        reason = data.get('reason')
        notes = data.get('notes', '')
        
        verification = UserIdentity.query.get(verification_id)
        if not verification:
            return jsonify({"success": False, "error": "Verification request not found"}), 404
        
        if verification.status != UserIdentity.PENDING:
            return jsonify({"success": False, "error": "Can only reject pending verification requests"}), 400
        
        verification.reject(current_user_id, reason, notes)
        db.session.commit()
        
        return jsonify({
            "success": True,
            "message": "Verification request rejected",
            "verification": verification.to_dict(include_admin_info=True)
        }), 200
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error rejecting verification: {e}")
        return jsonify({"success": False, "error": "Failed to reject verification"}), 500

@identity_bp.route('/admin/<int:verification_id>/revoke', methods=['POST'])
@jwt_required()
def revoke_verification(verification_id):
    """Revoke an approved verification (admin only)"""
    try:
        current_user_id = get_jwt_identity()
        current_user = User.query.get(current_user_id)
        
        if not current_user or not current_user.is_admin():
            return jsonify({"success": False, "error": "Access denied"}), 403
        
        data = request.get_json()
        if not data or not data.get('reason'):
            return jsonify({"success": False, "error": "Revocation reason is required"}), 400
        
        reason = data.get('reason')
        notes = data.get('notes', '')
        
        verification = UserIdentity.query.get(verification_id)
        if not verification:
            return jsonify({"success": False, "error": "Verification request not found"}), 404
        
        if verification.status != UserIdentity.APPROVED:
            return jsonify({"success": False, "error": "Can only revoke approved verifications"}), 400
        
        verification.revoke(current_user_id, reason, notes)
        db.session.commit()
        
        return jsonify({
            "success": True,
            "message": "Verification revoked successfully",
            "verification": verification.to_dict(include_admin_info=True)
        }), 200
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error revoking verification: {e}")
        return jsonify({"success": False, "error": "Failed to revoke verification"}), 500

# Display identity selection endpoints
@identity_bp.route('/posts/<int:post_id>/set-identity', methods=['POST'])
@jwt_required()
def set_post_display_identity(post_id):
    """Set display identity for a post"""
    try:
        current_user_id = get_jwt_identity()
        data = request.get_json() or {}
        
        # Get the post
        post = Post.query.filter_by(id=post_id, user_id=current_user_id, is_deleted=False).first()
        if not post:
            return jsonify({"success": False, "error": "Post not found or access denied"}), 404
        
        display_identity_id = data.get('display_identity_id')
        
        # If display_identity_id is None or 0, clear the identity (post as general user)
        if not display_identity_id:
            post.display_identity_id = None
            db.session.commit()
            
            return jsonify({
                "success": True,
                "message": "Post will be displayed as general user",
                "post": post.to_dict(include_author=True)
            }), 200
        
        # Validate the identity belongs to the user and is active
        user_identity = UserIdentity.query.filter_by(
            id=display_identity_id,
            user_id=current_user_id,
            status=UserIdentity.APPROVED
        ).first()
        
        if not user_identity or not user_identity.is_active():
            return jsonify({"success": False, "error": "Invalid or inactive identity"}), 400
        
        post.display_identity_id = display_identity_id
        db.session.commit()
        
        return jsonify({
            "success": True,
            "message": "Post display identity updated successfully",
            "post": post.to_dict(include_author=True)
        }), 200
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error setting post display identity: {e}")
        return jsonify({"success": False, "error": "Failed to set display identity"}), 500

@identity_bp.route('/comments/<int:comment_id>/set-identity', methods=['POST'])
@jwt_required()
def set_comment_display_identity(comment_id):
    """Set display identity for a comment"""
    try:
        current_user_id = get_jwt_identity()
        data = request.get_json() or {}
        
        # Get the comment
        comment = Comment.query.filter_by(id=comment_id, user_id=current_user_id, is_deleted=False).first()
        if not comment:
            return jsonify({"success": False, "error": "Comment not found or access denied"}), 404
        
        display_identity_id = data.get('display_identity_id')
        
        # If display_identity_id is None or 0, clear the identity (comment as general user)
        if not display_identity_id:
            comment.display_identity_id = None
            db.session.commit()
            
            return jsonify({
                "success": True,
                "message": "Comment will be displayed as general user",
                "comment": comment.to_dict(include_author=True)
            }), 200
        
        # Validate the identity belongs to the user and is active
        user_identity = UserIdentity.query.filter_by(
            id=display_identity_id,
            user_id=current_user_id,
            status=UserIdentity.APPROVED
        ).first()
        
        if not user_identity or not user_identity.is_active():
            return jsonify({"success": False, "error": "Invalid or inactive identity"}), 400
        
        comment.display_identity_id = display_identity_id
        db.session.commit()
        
        return jsonify({
            "success": True,
            "message": "Comment display identity updated successfully",
            "comment": comment.to_dict(include_author=True)
        }), 200
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error setting comment display identity: {e}")
        return jsonify({"success": False, "error": "Failed to set display identity"}), 500

@identity_bp.route('/gugu-messages/<int:message_id>/set-identity', methods=['POST'])
@jwt_required()
def set_gugu_message_display_identity(message_id):
    """Set display identity for a gugu message"""
    try:
        current_user_id = get_jwt_identity()
        data = request.get_json() or {}
        
        # Get the gugu message
        gugu_message = GuguMessage.query.filter_by(id=message_id, author_id=current_user_id).first()
        if not gugu_message:
            return jsonify({"success": False, "error": "Gugu message not found or access denied"}), 404
        
        display_identity_id = data.get('display_identity_id')
        
        # If display_identity_id is None or 0, clear the identity (message as general user)
        if not display_identity_id:
            gugu_message.display_identity_id = None
            db.session.commit()
            
            return jsonify({
                "success": True,
                "message": "Gugu message will be displayed as general user",
                "gugu_message": gugu_message.to_dict()
            }), 200
        
        # Validate the identity belongs to the user and is active
        user_identity = UserIdentity.query.filter_by(
            id=display_identity_id,
            user_id=current_user_id,
            status=UserIdentity.APPROVED
        ).first()
        
        if not user_identity or not user_identity.is_active():
            return jsonify({"success": False, "error": "Invalid or inactive identity"}), 400
        
        gugu_message.display_identity_id = display_identity_id
        db.session.commit()
        
        return jsonify({
            "success": True,
            "message": "Gugu message display identity updated successfully",
            "gugu_message": gugu_message.to_dict()
        }), 200
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error setting gugu message display identity: {e}")
        return jsonify({"success": False, "error": "Failed to set display identity"}), 500