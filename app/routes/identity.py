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
from app.services.admin_audit_service import log_admin_action
from app.utils.permissions import require_admin_user
import json

identity_bp = Blueprint('identity', __name__, url_prefix='/identities')
identity_admin_bp = Blueprint('identity_admin', __name__, url_prefix='/admin/identity')

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

@identity_bp.route('/requests', methods=['POST'])
@jwt_required()
def request_verification():
    """Request identity verification"""
    try:
        current_user_id = get_jwt_identity()
        data = request.get_json()
        
        if not data:
            return jsonify({"success": False, "error": "No data provided"}), 400
        
        identity_type_id = data.get('identity_type_id')
        verification_documents = data.get('verification_documents') or data.get('document_file_ids') or []
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
                        "filename": file_record.original_filename,
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
                            "filename": file_record.original_filename,
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

@identity_bp.route('/<int:verification_id>/withdraw', methods=['POST'])
@jwt_required()
def withdraw_verification_request(verification_id):
    """Withdraw a pending verification request"""
    try:
        current_user_id = get_jwt_identity()

        verification = UserIdentity.query.filter_by(
            id=verification_id,
            user_id=current_user_id
        ).first()

        if not verification:
            return jsonify({"success": False, "error": "Verification request not found"}), 404

        if verification.status != UserIdentity.PENDING:
            return jsonify({"success": False, "error": "Can only withdraw pending verification requests"}), 400

        db.session.delete(verification)
        db.session.commit()

        return jsonify({
            "success": True,
            "message": "Verification request withdrawn successfully"
        }), 200

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error withdrawing verification request: {e}")
        return jsonify({"success": False, "error": "Failed to withdraw verification request"}), 500

# Admin endpoints for identity verification management
def _identity_admin_counts():
    statuses = [
        UserIdentity.PENDING,
        UserIdentity.APPROVED,
        UserIdentity.REJECTED,
        UserIdentity.REVOKED,
    ]
    counts = {
        status: UserIdentity.query.filter_by(status=status).count()
        for status in statuses
    }
    counts["total"] = sum(counts.values())
    by_type_rows = (
        db.session.query(IdentityType.display_name, IdentityType.name, db.func.count(UserIdentity.id))
        .join(UserIdentity, UserIdentity.identity_type_id == IdentityType.id)
        .group_by(IdentityType.display_name, IdentityType.name)
        .all()
    )
    counts["by_type"] = {
        (display_name or name or "unknown"): int(total)
        for display_name, name, total in by_type_rows
    }
    return counts


def _identity_admin_guard():
    admin_user, error = require_admin_user()
    if error:
        response, status = error
        payload = response.get_json(silent=True) or {}
        return None, (jsonify({"success": False, "error": payload.get("error", "Access denied")}), status)
    return admin_user, None


def _identity_admin_requests_response():
    admin_user, error = _identity_admin_guard()
    if error:
        return error

    status = request.args.get('status')
    identity_type_id = request.args.get('identity_type_id')
    sort = request.args.get('sort', 'newest')

    try:
        page = max(1, int(request.args.get('page', 1)))
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "Invalid page"}), 400

    try:
        per_page = min(100, max(1, int(request.args.get('per_page', 20))))
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "Invalid per_page"}), 400

    valid_statuses = {
        UserIdentity.PENDING,
        UserIdentity.APPROVED,
        UserIdentity.REJECTED,
        UserIdentity.REVOKED,
    }
    if status and status not in valid_statuses:
        return jsonify({"success": False, "error": "Invalid status"}), 400

    query = UserIdentity.query
    if status:
        query = query.filter_by(status=status)

    if identity_type_id:
        try:
            identity_type_id_value = int(identity_type_id)
        except (TypeError, ValueError):
            return jsonify({"success": False, "error": "Invalid identity_type_id"}), 400
        query = query.filter_by(identity_type_id=identity_type_id_value)

    if sort == 'oldest':
        query = query.order_by(UserIdentity.created_at.asc())
    elif sort == 'priority':
        priority_case = db.case(
            (UserIdentity.status == UserIdentity.PENDING, 0),
            (UserIdentity.status == UserIdentity.APPROVED, 1),
            (UserIdentity.status == UserIdentity.REJECTED, 2),
            (UserIdentity.status == UserIdentity.REVOKED, 3),
            else_=4,
        )
        query = query.order_by(priority_case.asc(), UserIdentity.created_at.asc())
    else:
        query = query.order_by(UserIdentity.created_at.desc())

    pagination = query.paginate(page=page, per_page=per_page, error_out=False)

    return jsonify({
        "success": True,
        "requests": [item.to_admin_dict() for item in pagination.items],
        "total": pagination.total,
        "page": page,
        "pages": pagination.pages,
        "per_page": per_page,
        "counts": _identity_admin_counts(),
    }), 200


def _identity_pending_response():
    admin_user, error = _identity_admin_guard()
    if error:
        return error

    pending_verifications = UserIdentity.get_pending_verifications()
    return jsonify({
        "success": True,
        "pending_verifications": [verification.to_admin_dict() for verification in pending_verifications]
    }), 200


def _identity_action_response(verification_id, action):
    admin_user, error = _identity_admin_guard()
    if error:
        return error

    data = request.get_json() or {}
    notes = data.get('notes', '')
    verification = UserIdentity.query.get(verification_id)
    if not verification:
        return jsonify({"success": False, "error": "Verification request not found"}), 404

    try:
        if action == "approve":
            if verification.status != UserIdentity.PENDING:
                return jsonify({"success": False, "error": "Can only approve pending verification requests"}), 400
            expires_days = data.get('expires_days')
            if expires_days:
                from datetime import datetime, timezone, timedelta
                verification.expires_at = datetime.now(timezone.utc) + timedelta(days=expires_days)
            verification.approve(admin_user.id, notes)
            message = "Verification request approved successfully"
        elif action == "reject":
            if not data.get('reason'):
                return jsonify({"success": False, "error": "Rejection reason is required"}), 400
            if verification.status != UserIdentity.PENDING:
                return jsonify({"success": False, "error": "Can only reject pending verification requests"}), 400
            verification.reject(admin_user.id, data.get('reason'), notes)
            message = "Verification request rejected"
        elif action == "revoke":
            if not data.get('reason'):
                return jsonify({"success": False, "error": "Revocation reason is required"}), 400
            if verification.status != UserIdentity.APPROVED:
                return jsonify({"success": False, "error": "Can only revoke approved verifications"}), 400
            verification.revoke(admin_user.id, data.get('reason'), notes)
            message = "Verification revoked successfully"
        else:
            return jsonify({"success": False, "error": "Invalid identity admin action"}), 400

        log_admin_action(
            admin_user,
            f"identity.{action}",
            "user_identity",
            verification.id,
            verification.identity_type.display_name if verification.identity_type else None,
            notes or data.get('reason'),
            {
                "user_id": verification.user_id,
                "identity_type_id": verification.identity_type_id,
                "status": verification.status,
            },
        )
        db.session.commit()
        return jsonify({
            "success": True,
            "message": message,
            "verification": verification.to_dict(include_admin_info=True)
        }), 200
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error processing identity admin action: {e}")
        return jsonify({"success": False, "error": "Failed to update verification"}), 500


@identity_bp.route('/admin/requests', methods=['GET'])
@jwt_required()
def list_admin_verification_requests():
    """Get identity verification requests across all statuses (admin only)."""
    try:
        return _identity_admin_requests_response()

    except Exception as e:
        current_app.logger.error(f"Error fetching admin verification requests: {e}")
        return jsonify({"success": False, "error": "Failed to fetch verification requests"}), 500


@identity_bp.route('/admin/pending', methods=['GET'])
@jwt_required()
def get_pending_verifications():
    """Get all pending verification requests (admin only)"""
    try:
        return _identity_pending_response()
        
    except Exception as e:
        current_app.logger.error(f"Error fetching pending verifications: {e}")
        return jsonify({"success": False, "error": "Failed to fetch pending verifications"}), 500

@identity_bp.route('/admin/<int:verification_id>/approve', methods=['POST'])
@jwt_required()
def approve_verification(verification_id):
    """Approve a verification request (admin only)"""
    return _identity_action_response(verification_id, "approve")

@identity_bp.route('/admin/<int:verification_id>/reject', methods=['POST'])
@jwt_required()
def reject_verification(verification_id):
    """Reject a verification request (admin only)"""
    return _identity_action_response(verification_id, "reject")

@identity_bp.route('/admin/<int:verification_id>/revoke', methods=['POST'])
@jwt_required()
def revoke_verification(verification_id):
    """Revoke an approved verification (admin only)"""
    return _identity_action_response(verification_id, "revoke")


@identity_admin_bp.route('/requests', methods=['GET'])
@jwt_required()
def list_admin_identity_requests():
    return _identity_admin_requests_response()


@identity_admin_bp.route('/pending', methods=['GET'])
@jwt_required()
def list_pending_identity_requests():
    return _identity_pending_response()


@identity_admin_bp.route('/<int:verification_id>/approve', methods=['POST'])
@jwt_required()
def approve_identity_request(verification_id):
    return _identity_action_response(verification_id, "approve")


@identity_admin_bp.route('/<int:verification_id>/reject', methods=['POST'])
@jwt_required()
def reject_identity_request(verification_id):
    return _identity_action_response(verification_id, "reject")


@identity_admin_bp.route('/<int:verification_id>/revoke', methods=['POST'])
@jwt_required()
def revoke_identity_request(verification_id):
    return _identity_action_response(verification_id, "revoke")

# Display identity selection endpoints
@identity_bp.route('/display-identity', methods=['POST'])
@jwt_required()
def set_user_display_identity():
    """Set or clear the user's default display identity"""
    try:
        current_user_id = get_jwt_identity()
        data = request.get_json() or {}

        identity_id = data.get('identity_id')

        current_user = User.query.get(current_user_id)
        if not current_user:
            return jsonify({"success": False, "error": "User not found"}), 404

        # If identity_id is None or 0, clear the display identity
        if not identity_id:
            current_user.display_identity_id = None
            db.session.commit()
            return jsonify({
                "success": True,
                "message": "Display identity cleared"
            }), 200

        # Validate the identity belongs to the user and is active
        user_identity = UserIdentity.query.filter_by(
            id=identity_id,
            user_id=current_user_id,
            status=UserIdentity.APPROVED
        ).first()

        if not user_identity or not user_identity.is_active():
            return jsonify({"success": False, "error": "Invalid or inactive identity"}), 400

        current_user.display_identity_id = identity_id
        db.session.commit()

        return jsonify({
            "success": True,
            "message": "Display identity updated successfully",
            "display_identity_id": identity_id
        }), 200

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error setting user display identity: {e}")
        return jsonify({"success": False, "error": "Failed to set display identity"}), 500

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
