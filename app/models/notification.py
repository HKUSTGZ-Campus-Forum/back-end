# app/models/notification.py
from datetime import datetime, timezone
from app.extensions import db

class Notification(db.Model):
    __tablename__ = 'notifications'
    
    id = db.Column(db.Integer, primary_key=True)
    recipient_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    sender_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    type = db.Column(db.String(50), nullable=False)  # 'post_reaction', 'comment_reaction', 'post_comment', 'comment_reply'
    title = db.Column(db.String(255), nullable=False)
    message = db.Column(db.Text, nullable=False)
    read = db.Column(db.Boolean, default=False, nullable=False)
    
    # Reference to the related content
    post_id = db.Column(db.Integer, db.ForeignKey('posts.id'), nullable=True)
    comment_id = db.Column(db.Integer, db.ForeignKey('comments.id'), nullable=True)
    reaction_id = db.Column(db.Integer, db.ForeignKey('reactions.id'), nullable=True)
    
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
                          onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    # Relationships
    recipient = db.relationship('User', foreign_keys=[recipient_id], backref='notifications_received')
    sender = db.relationship('User', foreign_keys=[sender_id], backref='notifications_sent')
    post = db.relationship('Post', backref='notifications')
    comment = db.relationship('Comment', backref='notifications')
    reaction = db.relationship('Reaction', backref='notifications')

    # Add indexes for better performance
    __table_args__ = (
        db.Index('idx_notifications_recipient_read', 'recipient_id', 'read'),
        db.Index('idx_notifications_recipient_created', 'recipient_id', 'created_at'),
        db.Index('idx_notifications_type', 'type'),
    )

    def to_dict(self, include_sender=True, include_content=True):
        data = {
            "id": self.id,
            "recipient_id": self.recipient_id,
            "sender_id": self.sender_id,
            "type": self.type,
            "title": self.title,
            "message": self.message,
            "read": self.read,
            "post_id": self.post_id,
            "comment_id": self.comment_id,
            "reaction_id": self.reaction_id,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat()
        }
        
        # Include sender information
        if include_sender and self.sender:
            data["sender"] = {
                "id": self.sender.id,
                "username": self.sender.username,
                "avatar_url": self.sender.avatar_url
            }
        
        # Include content information for navigation
        if include_content:
            if self.post:
                data["post"] = {
                    "id": self.post.id,
                    "title": self.post.title
                }
            if self.comment:
                data["comment"] = {
                    "id": self.comment.id,
                    "content": self.comment.content[:100] + "..." if len(self.comment.content) > 100 else self.comment.content
                }
        
        return data

    @classmethod
    def create_notification(cls, recipient_id, sender_id, notification_type, title, message, 
                          post_id=None, comment_id=None, reaction_id=None):
        """Create a new notification"""
        # Don't create notification if sender and recipient are the same
        if sender_id == recipient_id:
            return None
            
        notification = cls(
            recipient_id=recipient_id,
            sender_id=sender_id,
            type=notification_type,
            title=title,
            message=message,
            post_id=post_id,
            comment_id=comment_id,
            reaction_id=reaction_id
        )
        
        db.session.add(notification)
        return notification

    @classmethod
    def get_unread_count(cls, user_id):
        """Get count of unread notifications for a user"""
        return cls.query.filter_by(recipient_id=user_id, read=False).count()

    def mark_as_read(self):
        """Mark notification as read"""
        self.read = True
        self.updated_at = datetime.now(timezone.utc)