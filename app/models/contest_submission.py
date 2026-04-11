from datetime import datetime, timezone
from app.extensions import db


class ContestSubmission(db.Model):
    __tablename__ = 'contest_submissions'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    project_name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=False)
    project_url = db.Column(db.String(500), nullable=True)
    team_members = db.Column(db.String(500), nullable=True)
    submitted_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    user = db.relationship('User', backref=db.backref('contest_submissions', lazy='dynamic'))

    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "username": self.user.username if self.user else None,
            "project_name": self.project_name,
            "description": self.description,
            "project_url": self.project_url,
            "team_members": self.team_members,
            "submitted_at": self.submitted_at.isoformat() if self.submitted_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
