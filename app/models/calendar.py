from datetime import datetime, timezone
from app.extensions import db

class UserCalendar(db.Model):
    __tablename__ = 'user_calendar'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    status = db.Column(db.String(50), nullable=False)
    date = db.Column(db.Date, nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        db.UniqueConstraint('user_id', 'date', name='unique_status_per_day'),
    )
    
    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "status": self.status,
            "date": self.date.isoformat(),
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat()
        } 