# app/services/notification_service.py
from app.models.notification import Notification
from app.models.user import User
from app.models.post import Post
from app.models.comment import Comment
from app.models.reaction import Reaction
from app.extensions import db

class NotificationService:
    
    @staticmethod
    def create_post_reaction_notification(reaction):
        """Create notification when someone reacts to a post"""
        if not reaction.post or not reaction.user:
            return None
            
        post = reaction.post
        sender = reaction.user
        recipient_id = post.user_id
        
        # Don't notify if user reacts to their own post
        if sender.id == recipient_id:
            return None
            
        title = f"{sender.username} reacted to your post"
        message = f"{sender.username} reacted to your post \"{post.title[:50]}{'...' if len(post.title) > 50 else ''}\""
        
        notification = Notification.create_notification(
            recipient_id=recipient_id,
            sender_id=sender.id,
            notification_type="post_reaction",
            title=title,
            message=message,
            post_id=post.id,
            reaction_id=reaction.id
        )
        
        # Send push notification
        if notification:
            NotificationService._send_push_notification(notification)
        
        return notification
    
    @staticmethod
    def create_comment_reaction_notification(reaction):
        """Create notification when someone reacts to a comment"""
        if not reaction.comment or not reaction.user:
            return None
            
        comment = reaction.comment
        sender = reaction.user
        recipient_id = comment.user_id
        
        # Don't notify if user reacts to their own comment
        if sender.id == recipient_id:
            return None
            
        title = f"{sender.username} reacted to your comment"
        message = f"{sender.username} reacted to your comment \"{comment.content[:50]}{'...' if len(comment.content) > 50 else ''}\""
        
        notification = Notification.create_notification(
            recipient_id=recipient_id,
            sender_id=sender.id,
            notification_type="comment_reaction",
            title=title,
            message=message,
            post_id=comment.post_id,
            comment_id=comment.id,
            reaction_id=reaction.id
        )
        
        # Send push notification
        if notification:
            NotificationService._send_push_notification(notification)
        
        return notification
    
    @staticmethod
    def create_post_comment_notification(comment):
        """Create notification when someone comments on a post"""
        if not comment.post or not comment.author:
            return None
            
        post = comment.post
        sender = comment.author
        recipient_id = post.user_id
        
        # Don't notify if user comments on their own post
        if sender.id == recipient_id:
            return None
            
        title = f"{sender.username} commented on your post"
        message = f"{sender.username} commented on your post \"{post.title[:50]}{'...' if len(post.title) > 50 else ''}\""
        
        notification = Notification.create_notification(
            recipient_id=recipient_id,
            sender_id=sender.id,
            notification_type="post_comment",
            title=title,
            message=message,
            post_id=post.id,
            comment_id=comment.id
        )
        
        # Send push notification
        if notification:
            NotificationService._send_push_notification(notification)
        
        return notification
    
    @staticmethod
    def create_comment_reply_notification(reply_comment):
        """Create notification when someone replies to a comment"""
        if not reply_comment.parent_comment_id or not reply_comment.author:
            return None
            
        parent_comment = Comment.query.get(reply_comment.parent_comment_id)
        if not parent_comment:
            return None
            
        sender = reply_comment.author
        recipient_id = parent_comment.user_id
        
        # Don't notify if user replies to their own comment
        if sender.id == recipient_id:
            return None
            
        title = f"{sender.username} replied to your comment"
        message = f"{sender.username} replied to your comment \"{parent_comment.content[:50]}{'...' if len(parent_comment.content) > 50 else ''}\""
        
        notification = Notification.create_notification(
            recipient_id=recipient_id,
            sender_id=sender.id,
            notification_type="comment_reply",
            title=title,
            message=message,
            post_id=reply_comment.post_id,
            comment_id=reply_comment.id
        )
        
        # Send push notification
        if notification:
            NotificationService._send_push_notification(notification)
        
        return notification
    
    @staticmethod
    def _send_push_notification(notification):
        """Send push notification for a database notification"""
        try:
            from app.services.push_service import PushService
            result = PushService.send_notification_push(notification)
            if result['success']:
                print(f"Push notification sent for notification {notification.id}")
            else:
                print(f"Failed to send push notification for notification {notification.id}: {result}")
        except Exception as e:
            print(f"Error sending push notification: {str(e)}")
            # Don't fail the main notification creation if push fails
            pass
    
    @staticmethod
    def get_user_notifications(user_id, page=1, limit=20, unread_only=False):
        """Get paginated notifications for a user"""
        query = Notification.query.filter_by(recipient_id=user_id)
        
        if unread_only:
            query = query.filter_by(read=False)
            
        query = query.order_by(Notification.created_at.desc())
        
        paginated = query.paginate(page=page, per_page=limit, error_out=False)
        
        return {
            "notifications": [notification.to_dict() for notification in paginated.items],
            "total_count": paginated.total,
            "total_pages": paginated.pages,
            "current_page": page,
            "unread_count": Notification.get_unread_count(user_id)
        }
    
    @staticmethod
    def mark_all_as_read(user_id):
        """Mark all notifications as read for a user"""
        notifications = Notification.query.filter_by(recipient_id=user_id, read=False).all()
        
        for notification in notifications:
            notification.mark_as_read()
            
        db.session.commit()
        return len(notifications)