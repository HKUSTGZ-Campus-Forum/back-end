# app/services/push_service.py
import json
import requests
from typing import List, Dict, Any
from pywebpush import webpush, WebPushException
from app.models.push_subscription import PushSubscription
from app.models.notification import Notification
from app.extensions import db
from flask import current_app

class PushService:
    """Service for handling Web Push notifications"""
    
    @staticmethod
    def get_vapid_keys():
        """Get VAPID keys from configuration"""
        try:
            return {
                'private_key': current_app.config.get('VAPID_PRIVATE_KEY'),
                'public_key': current_app.config.get('VAPID_PUBLIC_KEY'),
                'claims': {"sub": f"mailto:{current_app.config.get('VAPID_EMAIL', 'admin@example.com')}"}
            }
        except Exception as e:
            current_app.logger.error(f"Failed to get VAPID keys: {e}")
            return None
    
    @staticmethod
    def send_notification_to_user(user_id: int, notification_data: Dict[str, Any]) -> Dict[str, Any]:
        """Send push notification to all active subscriptions of a user"""
        
        # Get all active subscriptions for the user
        subscriptions = PushSubscription.get_active_subscriptions(user_id)
        
        if not subscriptions:
            return {"success": False, "message": "No active subscriptions found"}
        
        vapid_keys = PushService.get_vapid_keys()
        if not vapid_keys:
            return {"success": False, "message": "VAPID keys not configured"}
        
        results = []
        successful_sends = 0
        
        for subscription in subscriptions:
            try:
                # Prepare subscription info for pywebpush
                subscription_info = {
                    "endpoint": subscription.endpoint,
                    "keys": {
                        "p256dh": subscription.p256dh_key,
                        "auth": subscription.auth_key
                    }
                }
                
                # Send the push notification
                response = webpush(
                    subscription_info=subscription_info,
                    data=json.dumps(notification_data),
                    vapid_private_key=vapid_keys['private_key'],
                    vapid_claims=vapid_keys['claims']
                )
                
                # Update last used timestamp
                subscription.update_last_used()
                
                results.append({
                    "subscription_id": subscription.id,
                    "success": True,
                    "status_code": response.status_code
                })
                successful_sends += 1
                
            except WebPushException as e:
                current_app.logger.warning(f"WebPush failed for subscription {subscription.id}: {e}")
                
                # Handle subscription errors (410 = Gone, subscription invalid)
                if e.response and e.response.status_code == 410:
                    subscription.is_active = False
                    db.session.commit()
                    current_app.logger.info(f"Deactivated invalid subscription {subscription.id}")
                
                results.append({
                    "subscription_id": subscription.id,
                    "success": False,
                    "error": str(e),
                    "status_code": e.response.status_code if e.response else None
                })
                
            except Exception as e:
                current_app.logger.error(f"Unexpected error sending push to subscription {subscription.id}: {e}")
                results.append({
                    "subscription_id": subscription.id,
                    "success": False,
                    "error": str(e)
                })
        
        return {
            "success": successful_sends > 0,
            "total_subscriptions": len(subscriptions),
            "successful_sends": successful_sends,
            "results": results
        }
    
    @staticmethod
    def send_notification_push(notification: Notification) -> Dict[str, Any]:
        """Send push notification for a database notification"""
        
        # Get current unread count for the user
        from app.models.notification import Notification as NotificationModel
        unread_count = NotificationModel.get_unread_count(notification.recipient_id)
        
        # Prepare notification data for push
        notification_data = {
            "title": notification.title,
            "body": notification.message,
            "icon": "/image/uniKorn.png",
            "badge": "/image/uniKorn.png",
            "unread_count": unread_count,  # Add unread count for badge
            "data": {
                "notificationId": notification.id,
                "type": notification.type,
                "url": PushService._get_notification_url(notification),
                "timestamp": notification.created_at.isoformat(),
                "post_id": notification.post_id,
                "comment_id": notification.comment_id,
                "unread_count": unread_count
            },
            "actions": [
                {
                    "action": "view",
                    "title": "查看",
                    "icon": "/image/uniKorn.png"
                },
                {
                    "action": "dismiss",
                    "title": "关闭"
                }
            ],
            "requireInteraction": False,  # Don't require user interaction to auto-dismiss
            "tag": f"notification-{notification.id}"  # Prevent duplicate notifications
        }
        
        return PushService.send_notification_to_user(
            notification.recipient_id, 
            notification_data
        )
    
    @staticmethod
    def _get_notification_url(notification: Notification) -> str:
        """Get the URL to navigate to when notification is clicked"""
        if notification.post_id:
            return f"/forum/posts/{notification.post_id}"
        elif notification.comment_id and notification.post:
            return f"/forum/posts/{notification.post.id}#comment-{notification.comment_id}"
        return "/notifications"
    
    @staticmethod
    def test_push_notification(user_id: int) -> Dict[str, Any]:
        """Send a test push notification to a user"""
        test_data = {
            "title": "测试通知",
            "body": "这是一个测试推送通知，用于验证推送功能是否正常工作。",
            "icon": "/icons/topbar_logo.svg",
            "badge": "/favicon.ico",
            "data": {
                "test": True,
                "timestamp": "now",
                "url": "/notifications"
            },
            "actions": [
                {
                    "action": "view",
                    "title": "查看通知"
                }
            ],
            "tag": "test-notification"
        }
        
        return PushService.send_notification_to_user(user_id, test_data)
    
    @staticmethod
    def send_badge_update(user_id: int, unread_count: int) -> Dict[str, Any]:
        """Send a silent push notification to update app badge only"""
        badge_data = {
            "title": "",  # Silent notification
            "body": "",   # Silent notification
            "silent": True,
            "unread_count": unread_count,
            "data": {
                "badge_update": True,
                "unread_count": unread_count,
                "timestamp": "now"
            },
            "tag": "badge-update"
        }
        
        return PushService.send_notification_to_user(user_id, badge_data)