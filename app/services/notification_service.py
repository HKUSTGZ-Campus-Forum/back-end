# app/services/notification_service.py
from app.models.notification import Notification
from app.models.user import User
from app.models.user_role import UserRole
from app.models.post import Post
from app.models.comment import Comment
from app.models.reaction import Reaction
from app.extensions import db

class NotificationService:
    @staticmethod
    def _finalize_notification(notification):
        if notification:
            NotificationService._send_push_notification(notification)
        return notification

    @staticmethod
    def _create_feedback_notification(
        recipient_id,
        sender_id,
        notification_type,
        title,
        message,
        link_url,
    ):
        notification = Notification.create_notification(
            recipient_id=recipient_id,
            sender_id=sender_id,
            notification_type=notification_type,
            title=title,
            message=message,
            link_url=link_url,
        )
        return NotificationService._finalize_notification(notification)

    @staticmethod
    def _admin_users():
        return (
            User.query.join(User.role)
            .filter(UserRole.name == UserRole.ADMIN, User.is_deleted.is_(False))
            .all()
        )
    
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
        
        return NotificationService._finalize_notification(notification)
    
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
        
        return NotificationService._finalize_notification(notification)
    
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
        
        return NotificationService._finalize_notification(notification)
    
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
        
        return NotificationService._finalize_notification(notification)

    @staticmethod
    def create_feedback_published_notification(feedback, admin_user_id=None):
        return NotificationService._create_feedback_notification(
            recipient_id=feedback.author_id,
            sender_id=admin_user_id,
            notification_type="feedback_published",
            title="你的反馈已公开",
            message=f"反馈《{feedback.title}》已经通过审核并公开展示。",
            link_url=f"/feedback/{feedback.id}",
        )

    @staticmethod
    def create_feedback_rejected_notification(feedback, admin_user_id=None, note=None):
        note_suffix = f" 原因：{note}" if note else ""
        return NotificationService._create_feedback_notification(
            recipient_id=feedback.author_id,
            sender_id=admin_user_id,
            notification_type="feedback_rejected",
            title="你的反馈未通过审核",
            message=f"反馈《{feedback.title}》未通过审核。{note_suffix}".strip(),
            link_url=f"/feedback/{feedback.id}",
        )

    @staticmethod
    def create_feedback_merge_request_created_notification(merge_request, sender_id=None):
        return NotificationService._create_feedback_notification(
            recipient_id=merge_request.feedback.author_id,
            sender_id=sender_id or merge_request.author_id,
            notification_type="feedback_merge_request_created",
            title="有人发起了新的 merge 申请",
            message=f"反馈《{merge_request.feedback.title}》收到了一条新的公开 merge 申请。",
            link_url=f"/feedback/merge-requests/{merge_request.id}",
        )

    @staticmethod
    def create_feedback_merge_request_changes_requested_notification(merge_request, sender_id=None):
        return NotificationService._create_feedback_notification(
            recipient_id=merge_request.author_id,
            sender_id=sender_id,
            notification_type="feedback_merge_request_changes_requested",
            title="你的 merge 申请被要求继续修改",
            message=f"反馈《{merge_request.feedback.title}》的作者要求继续调整这条 merge 申请。",
            link_url=f"/feedback/merge-requests/{merge_request.id}",
        )

    @staticmethod
    def create_feedback_merge_request_rejected_by_author_notification(merge_request, sender_id=None):
        return NotificationService._create_feedback_notification(
            recipient_id=merge_request.author_id,
            sender_id=sender_id,
            notification_type="feedback_merge_request_rejected_by_author",
            title="你的 merge 申请被作者拒绝",
            message=f"反馈《{merge_request.feedback.title}》的作者拒绝了这条 merge 申请。",
            link_url=f"/feedback/merge-requests/{merge_request.id}",
        )

    @staticmethod
    def create_feedback_merge_request_ready_for_admin_notification(merge_request, sender_id=None):
        notifications = []
        for admin_user in NotificationService._admin_users():
            notification = NotificationService._create_feedback_notification(
                recipient_id=admin_user.id,
                sender_id=sender_id or merge_request.feedback.author_id,
                notification_type="feedback_merge_request_ready_for_admin",
                title="有新的 merge 申请等待终审",
                message=f"反馈《{merge_request.feedback.title}》有一条 merge 申请等待管理员终审。",
                link_url="/admin/feedback",
            )
            if notification:
                notifications.append(notification)
        return notifications

    @staticmethod
    def create_feedback_merge_request_merged_notifications(merge_request, sender_id=None):
        notifications = []
        for recipient_id in {merge_request.feedback.author_id, merge_request.author_id}:
            notification = NotificationService._create_feedback_notification(
                recipient_id=recipient_id,
                sender_id=sender_id,
                notification_type="feedback_merge_request_merged",
                title="merge 申请已通过并合并",
                message=f"反馈《{merge_request.feedback.title}》的一条 merge 申请已经完成合并。",
                link_url=f"/feedback/{merge_request.feedback_id}",
            )
            if notification:
                notifications.append(notification)
        return notifications

    @staticmethod
    def create_feedback_merge_request_rejected_by_admin_notifications(merge_request, sender_id=None):
        notifications = []
        for recipient_id in {merge_request.feedback.author_id, merge_request.author_id}:
            notification = NotificationService._create_feedback_notification(
                recipient_id=recipient_id,
                sender_id=sender_id,
                notification_type="feedback_merge_request_rejected_by_admin",
                title="merge 申请未通过管理员终审",
                message=f"反馈《{merge_request.feedback.title}》的一条 merge 申请未通过管理员终审。",
                link_url=f"/feedback/merge-requests/{merge_request.id}",
            )
            if notification:
                notifications.append(notification)
        return notifications
    
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
