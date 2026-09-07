# app/routes/notification.py
from flask import Blueprint, request, jsonify
from app.models.notification import Notification
from app.services.notification_service import NotificationService
from app.extensions import db
from flask_jwt_extended import jwt_required, get_jwt_identity
from datetime import datetime, timezone

bp = Blueprint('notification', __name__, url_prefix='/notifications')

# Get user's notifications
@bp.route('', methods=['GET'])
@jwt_required()
def get_notifications():
    """Get paginated notifications for the current user"""
    try:
        user_id = get_jwt_identity()
        
        # Get pagination parameters
        page = request.args.get('page', 1, type=int)
        limit = request.args.get('limit', 20, type=int)
        unread_only = request.args.get('unread_only', 'false').lower() == 'true'
        
        # Limit the max page size
        limit = min(limit, 50)
        
        # Get notifications using service
        result = NotificationService.get_user_notifications(
            user_id=user_id,
            page=page,
            limit=limit,
            unread_only=unread_only
        )
        
        return jsonify(result), 200
        
    except Exception as e:
        return jsonify({"error": f"An error occurred: {str(e)}"}), 500

# Get unread notifications count
@bp.route('/unread-count', methods=['GET'])
@jwt_required()
def get_unread_count():
    """Get count of unread notifications for the current user"""
    try:
        user_id = get_jwt_identity()
        count = Notification.get_unread_count(user_id)
        
        return jsonify({"unread_count": count}), 200
        
    except Exception as e:
        return jsonify({"error": f"An error occurred: {str(e)}"}), 500

# Mark a specific notification as read
@bp.route('/<int:notification_id>/read', methods=['PUT'])
@jwt_required()
def mark_notification_read(notification_id):
    """Mark a specific notification as read"""
    try:
        user_id = get_jwt_identity()
        
        # Find the notification and verify ownership
        notification = Notification.query.filter_by(
            id=notification_id, 
            recipient_id=user_id
        ).first()
        
        if not notification:
            return jsonify({"error": "Notification not found"}), 404
        
        # Mark as read
        notification.mark_as_read()
        db.session.commit()
        
        return jsonify(notification.to_dict()), 200
        
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"An error occurred: {str(e)}"}), 500

# Mark all notifications as read
@bp.route('/mark-all-read', methods=['PUT'])
@jwt_required()
def mark_all_notifications_read():
    """Mark all notifications as read for the current user"""
    try:
        user_id = get_jwt_identity()
        
        # Mark all as read using service
        count = NotificationService.mark_all_as_read(user_id)
        
        return jsonify({"message": f"Marked {count} notifications as read"}), 200
        
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"An error occurred: {str(e)}"}), 500

# Delete a specific notification
@bp.route('/<int:notification_id>', methods=['DELETE'])
@jwt_required()
def delete_notification(notification_id):
    """Delete a specific notification"""
    try:
        user_id = get_jwt_identity()
        
        # Find the notification and verify ownership
        notification = Notification.query.filter_by(
            id=notification_id, 
            recipient_id=user_id
        ).first()
        
        if not notification:
            return jsonify({"error": "Notification not found"}), 404
        
        # Delete the notification
        db.session.delete(notification)
        db.session.commit()
        
        return "", 204
        
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"An error occurred: {str(e)}"}), 500

# Get notification by ID (for navigation)
@bp.route('/<int:notification_id>', methods=['GET'])
@jwt_required()
def get_notification(notification_id):
    """Get a specific notification by ID"""
    try:
        user_id = get_jwt_identity()
        
        # Find the notification and verify ownership
        notification = Notification.query.filter_by(
            id=notification_id, 
            recipient_id=user_id
        ).first()
        
        if not notification:
            return jsonify({"error": "Notification not found"}), 404
        
        # Automatically mark as read when viewed
        if not notification.read:
            notification.mark_as_read()
            db.session.commit()
        
        return jsonify(notification.to_dict()), 200
        
    except Exception as e:
        return jsonify({"error": f"An error occurred: {str(e)}"}), 500