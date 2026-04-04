from datetime import datetime, timezone
from app.extensions import db


class ContestOrganizer(db.Model):
    __tablename__ = 'contest_organizers'

    id = db.Column(db.Integer, primary_key=True)
    contest_id = db.Column(db.Integer, db.ForeignKey('contest_info.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    added_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # 同一个用户在同一个比赛里只能是一次 organizer
    __table_args__ = (
        db.UniqueConstraint('contest_id', 'user_id', name='uq_contest_organizer'),
    )

    user = db.relationship('User', backref=db.backref('organized_contests', lazy='dynamic'))
    contest = db.relationship('ContestInfo', backref=db.backref('organizers', lazy='dynamic'))

    def to_dict(self):
        return {
            "id": self.id,
            "contest_id": self.contest_id,
            "user_id": self.user_id,
            "username": self.user.username if self.user else None,
            "added_at": self.added_at.isoformat() if self.added_at else None,
        }
