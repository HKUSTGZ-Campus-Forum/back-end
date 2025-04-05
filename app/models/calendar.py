from datetime import datetime, timezone
from app.extensions import db

class UserCalendar(db.Model):
    __tablename__ = 'user_calendar'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    emoji_id = db.Column(db.Integer, db.ForeignKey('calendar_emojis.id', ondelete='RESTRICT'), nullable=False)
    date = db.Column(db.Date, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    # Update the __table_args__ section
    __table_args__ = (
        db.UniqueConstraint(
            'user_id', 'date',
            name='uq_user_calendar_user_date'
        ),
        db.Index(
            'idx_user_calendar_user_recent',
            'user_id', 'date',
            postgresql_where=db.text("date >= CURRENT_DATE - INTERVAL '30 days'")
        ),
    )
    
    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "emoji_id": self.emoji_id,
            "date": self.date.isoformat(),
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat()
        }