from datetime import datetime, timezone

from app.extensions import db


class FeedbackVersion(db.Model):
    __tablename__ = "feedback_versions"

    id = db.Column(db.Integer, primary_key=True)
    feedback_id = db.Column(db.Integer, db.ForeignKey("feedbacks.id"), nullable=False, index=True)
    version_number = db.Column(db.Integer, nullable=False)
    markdown_content = db.Column(db.Text, nullable=False)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    source_merge_request_id = db.Column(
        db.Integer,
        db.ForeignKey("feedback_merge_requests.id"),
        nullable=True,
    )
    created_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    created_by = db.relationship(
        "User",
        backref=db.backref("feedback_versions_created", lazy="dynamic"),
        foreign_keys=[created_by_user_id],
    )
    source_merge_request = db.relationship(
        "FeedbackMergeRequest",
        foreign_keys=[source_merge_request_id],
        uselist=False,
    )

    __table_args__ = (
        db.UniqueConstraint(
            "feedback_id",
            "version_number",
            name="uq_feedback_versions_feedback_version_number",
        ),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "feedback_id": self.feedback_id,
            "version_number": self.version_number,
            "markdown_content": self.markdown_content,
            "created_by_user_id": self.created_by_user_id,
            "source_merge_request_id": self.source_merge_request_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
