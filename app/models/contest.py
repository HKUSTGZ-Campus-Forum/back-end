from datetime import datetime, timezone
from app.extensions import db


class ContestInfo(db.Model):
    __tablename__ = 'contest_info'

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False, default='百块奖金Web大赛')
    description = db.Column(db.Text, nullable=False, default='')
    rules = db.Column(db.Text, nullable=False, default='')
    prizes = db.Column(db.Text, nullable=False, default='')
    start_time = db.Column(db.DateTime(timezone=True), nullable=True)
    end_time = db.Column(db.DateTime(timezone=True), nullable=True)
    announcements = db.Column(db.Text, nullable=False, default='')
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    updated_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    def is_organizer(self, user_id: int) -> bool:
        """判断指定用户是否是本比赛的 organizer"""
        from app.models.contest_organizer import ContestOrganizer
        return ContestOrganizer.query.filter_by(
            contest_id=self.id, user_id=user_id
        ).first() is not None

    def to_dict(self, include_organizers: bool = False):
        data = {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "rules": self.rules,
            "prizes": self.prizes,
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "announcements": self.announcements,
            "is_active": self.is_active,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
        if include_organizers:
            data["organizers"] = [o.to_dict() for o in self.organizers]
        return data
