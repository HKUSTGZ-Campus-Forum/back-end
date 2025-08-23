# app/routes/push.py
from flask import Blueprint, request, jsonify
from app.models.push_subscription import PushSubscription
from app.services.push_service import PushService
from app.extensions import db
from flask_jwt_extended import jwt_required, get_jwt_identity

bp = Blueprint('push', __name__, url_prefix='/push')

# Get VAPID public key for client subscription
@bp.route('/vapid-public-key', methods=['GET'])
def get_vapid_public_key():
    """Get the VAPID public key for client-side subscription"""
    try:
        from flask import current_app
        public_key = current_app.config.get('VAPID_PUBLIC_KEY')
        
        if not public_key:
            return jsonify({"error": "VAPID public key not configured"}), 500
        
        return jsonify({"vapid_public_key": public_key}), 200
        
    except Exception as e:
        return jsonify({"error": f"An error occurred: {str(e)}"}), 500

# Subscribe to push notifications
@bp.route('/subscribe', methods=['POST'])
@jwt_required()
def subscribe_to_push():
    """Subscribe user to push notifications"""
    try:
        user_id = get_jwt_identity()
        data = request.get_json() or {}
        
        # Validate required fields
        if not data.get('endpoint'):
            return jsonify({"error": "endpoint is required"}), 400
        
        if not data.get('keys') or not data['keys'].get('p256dh') or not data['keys'].get('auth'):
            return jsonify({"error": "keys.p256dh and keys.auth are required"}), 400
        
        # Get user agent for debugging
        user_agent = request.headers.get('User-Agent', '')
        
        # Create or update subscription
        subscription = PushSubscription.get_or_create_subscription(
            user_id=user_id,
            endpoint=data['endpoint'],
            p256dh_key=data['keys']['p256dh'],
            auth_key=data['keys']['auth'],
            user_agent=user_agent
        )
        
        db.session.commit()
        
        return jsonify({
            "message": "Push subscription created successfully",
            "subscription": subscription.to_dict()
        }), 201
        
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"An error occurred: {str(e)}"}), 500

# Unsubscribe from push notifications
@bp.route('/unsubscribe', methods=['POST'])
@jwt_required()
def unsubscribe_from_push():
    """Unsubscribe user from push notifications"""
    try:
        user_id = get_jwt_identity()
        data = request.get_json() or {}
        
        endpoint = data.get('endpoint')
        
        if endpoint:
            # Unsubscribe specific endpoint
            subscription = PushSubscription.query.filter_by(
                user_id=user_id, 
                endpoint=endpoint
            ).first()
            
            if subscription:
                subscription.is_active = False
                db.session.commit()
                return jsonify({"message": "Unsubscribed from push notifications"}), 200
            else:
                return jsonify({"error": "Subscription not found"}), 404
        else:
            # Unsubscribe all endpoints for user
            subscriptions = PushSubscription.query.filter_by(user_id=user_id, is_active=True).all()
            
            for subscription in subscriptions:
                subscription.is_active = False
            
            db.session.commit()
            
            return jsonify({
                "message": f"Unsubscribed {len(subscriptions)} push subscriptions"
            }), 200
        
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"An error occurred: {str(e)}"}), 500

# Get user's push subscriptions
@bp.route('/subscriptions', methods=['GET'])
@jwt_required()
def get_push_subscriptions():
    """Get user's push subscriptions"""
    try:
        user_id = get_jwt_identity()
        
        subscriptions = PushSubscription.query.filter_by(user_id=user_id).all()
        
        return jsonify({
            "subscriptions": [sub.to_dict() for sub in subscriptions],
            "active_count": len([sub for sub in subscriptions if sub.is_active])
        }), 200
        
    except Exception as e:
        return jsonify({"error": f"An error occurred: {str(e)}"}), 500

# Test push notification
@bp.route('/test', methods=['POST'])
@jwt_required()
def test_push_notification():
    """Send a test push notification to the current user"""
    try:
        user_id = get_jwt_identity()
        
        result = PushService.test_push_notification(user_id)
        
        if result['success']:
            return jsonify({
                "message": "Test notification sent successfully",
                "result": result
            }), 200
        else:
            return jsonify({
                "message": "Failed to send test notification",
                "result": result
            }), 400
        
    except Exception as e:
        return jsonify({"error": f"An error occurred: {str(e)}"}), 500

# Admin endpoint to test push to specific user
@bp.route('/test/<int:target_user_id>', methods=['POST'])
@jwt_required()
def test_push_to_user(target_user_id):
    """Send a test push notification to a specific user (admin only)"""
    try:
        current_user_id = get_jwt_identity()
        
        # TODO: Add admin check here
        # from app.models.user import User
        # current_user = User.query.get(current_user_id)
        # if not current_user.is_admin():
        #     return jsonify({"error": "Admin access required"}), 403
        
        result = PushService.test_push_notification(target_user_id)
        
        return jsonify({
            "message": f"Test notification sent to user {target_user_id}",
            "result": result
        }), 200
        
    except Exception as e:
        return jsonify({"error": f"An error occurred: {str(e)}"}), 500