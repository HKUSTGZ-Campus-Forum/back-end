from datetime import datetime, timezone
from app.extensions import db

class CalendarEmoji(db.Model):
    __tablename__ = 'calendar_emojis'
    
    id = db.Column(db.Integer, primary_key=True)
    emoji_code = db.Column(db.String(50), nullable=False, unique=True)
    description = db.Column(db.Text)
    image_url = db.Column(db.Text)
    display_order = db.Column(db.Integer, default=0, nullable=False)
    probability = db.Column(db.Numeric(5, 4), default=0.0, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    
    # Relationship
    calendar_entries = db.relationship('UserCalendar', backref='emoji', lazy='dynamic')
    
    __table_args__ = (
        db.CheckConstraint('probability >= 0.0 AND probability <= 1.0', name='valid_probability'),
    )
    
    def to_dict(self):
        return {
            "id": self.id,
            "emoji_code": self.emoji_code,
            "description": self.description,
            "image_url": self.image_url,
            "display_order": self.display_order,
            "probability": float(self.probability),
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat()
        } 