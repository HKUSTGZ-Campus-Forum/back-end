# app/models/push_subscription.py
from datetime import datetime, timezone
from app.extensions import db

class PushSubscription(db.Model):
    __tablename__ = 'push_subscriptions'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    endpoint = db.Column(db.Text, nullable=False)
    p256dh_key = db.Column(db.Text, nullable=False)  # Public key for encryption
    auth_key = db.Column(db.Text, nullable=False)    # Auth secret for encryption
    user_agent = db.Column(db.Text, nullable=True)    # Browser info for debugging
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
                          onupdate=lambda: datetime.now(timezone.utc), nullable=False)
    last_used_at = db.Column(db.DateTime(timezone=True), nullable=True)

    # Relationships
    user = db.relationship('User', backref='push_subscriptions')

    # Add unique constraint for user and endpoint
    __table_args__ = (
        db.UniqueConstraint('user_id', 'endpoint', name='uq_user_endpoint'),
        db.Index('idx_push_subscriptions_user_active', 'user_id', 'is_active'),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "endpoint": self.endpoint,
            "keys": {
                "p256dh": self.p256dh_key,
                "auth": self.auth_key
            },
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "last_used_at": self.last_used_at.isoformat() if self.last_used_at else None
        }

    def update_last_used(self):
        """Update last used timestamp"""
        self.last_used_at = datetime.now(timezone.utc)
        db.session.commit()

    @classmethod
    def get_active_subscriptions(cls, user_id):
        """Get all active subscriptions for a user"""
        return cls.query.filter_by(user_id=user_id, is_active=True).all()

    @classmethod
    def get_or_create_subscription(cls, user_id, endpoint, p256dh_key, auth_key, user_agent=None):
        """Get existing subscription or create new one"""
        # Try to find existing subscription
        existing = cls.query.filter_by(user_id=user_id, endpoint=endpoint).first()
        
        if existing:
            # Update existing subscription
            existing.p256dh_key = p256dh_key
            existing.auth_key = auth_key
            existing.user_agent = user_agent
            existing.is_active = True
            existing.updated_at = datetime.now(timezone.utc)
            return existing
        else:
            # Create new subscription
            subscription = cls(
                user_id=user_id,
                endpoint=endpoint,
                p256dh_key=p256dh_key,
                auth_key=auth_key,
                user_agent=user_agent
            )
            db.session.add(subscription)
            return subscription