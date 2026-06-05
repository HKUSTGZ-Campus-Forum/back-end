from datetime import datetime, timezone

from app.extensions import db


class FeedbackAuditEvent(db.Model):
    __tablename__ = "feedback_audit_events"

    id = db.Column(db.Integer, primary_key=True)
    feedback_id = db.Column(db.Integer, db.ForeignKey("feedbacks.id"), nullable=False, index=True)
    merge_request_id = db.Column(
        db.Integer,
        db.ForeignKey("feedback_merge_requests.id"),
        nullable=True,
        index=True,
    )
    actor_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    event_type = db.Column(db.String(80), nullable=False, index=True)
    event_payload = db.Column(db.JSON, nullable=False, default=dict)
    created_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    actor = db.relationship(
        "User",
        backref=db.backref("feedback_audit_events", lazy="dynamic"),
        foreign_keys=[actor_user_id],
    )

    def to_dict(self):
        return {
            "id": self.id,
            "feedback_id": self.feedback_id,
            "merge_request_id": self.merge_request_id,
            "actor_user_id": self.actor_user_id,
            "event_type": self.event_type,
            "event_payload": self.event_payload,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
