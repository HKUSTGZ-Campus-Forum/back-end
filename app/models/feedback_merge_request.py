from datetime import datetime, timezone

from app.extensions import db


class FeedbackMergeRequest(db.Model):
    __tablename__ = "feedback_merge_requests"

    STATUS_OPEN = "open"
    STATUS_AUTHOR_CHANGES_REQUESTED = "author_changes_requested"
    STATUS_AUTHOR_REJECTED = "author_rejected"
    STATUS_AUTHOR_ACCEPTED_PENDING_ADMIN = "author_accepted_pending_admin"
    STATUS_ADMIN_REJECTED = "admin_rejected"
    STATUS_MERGED = "merged"
    STATUS_WITHDRAWN = "withdrawn"

    id = db.Column(db.Integer, primary_key=True)
    feedback_id = db.Column(db.Integer, db.ForeignKey("feedbacks.id"), nullable=False, index=True)
    author_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    base_version_id = db.Column(db.Integer, db.ForeignKey("feedback_versions.id"), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    change_summary = db.Column(db.Text, nullable=True)
    proposed_markdown_content = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(50), nullable=False, default=STATUS_OPEN, index=True)
    author_reviewed_at = db.Column(db.DateTime(timezone=True), nullable=True)
    author_review_note = db.Column(db.Text, nullable=True)
    admin_reviewed_at = db.Column(db.DateTime(timezone=True), nullable=True)
    admin_review_note = db.Column(db.Text, nullable=True)
    merged_version_id = db.Column(db.Integer, db.ForeignKey("feedback_versions.id"), nullable=True)
    created_at = db.Column(
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

    author = db.relationship(
        "User",
        backref=db.backref("feedback_merge_requests_authored", lazy="dynamic"),
        foreign_keys=[author_id],
    )
    base_version = db.relationship(
        "FeedbackVersion",
        foreign_keys=[base_version_id],
        uselist=False,
    )
    merged_version = db.relationship(
        "FeedbackVersion",
        foreign_keys=[merged_version_id],
        uselist=False,
        post_update=True,
    )
    comments = db.relationship(
        "FeedbackMergeComment",
        foreign_keys="FeedbackMergeComment.merge_request_id",
        backref=db.backref("merge_request", lazy="joined"),
        lazy="dynamic",
        cascade="all, delete-orphan",
    )
    audit_events = db.relationship(
        "FeedbackAuditEvent",
        foreign_keys="FeedbackAuditEvent.merge_request_id",
        backref=db.backref("merge_request", lazy="joined"),
        lazy="dynamic",
    )

    __table_args__ = (
        db.Index(
            "idx_feedback_merge_requests_feedback_status",
            "feedback_id",
            "status",
        ),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "feedback_id": self.feedback_id,
            "author_id": self.author_id,
            "base_version_id": self.base_version_id,
            "title": self.title,
            "change_summary": self.change_summary,
            "proposed_markdown_content": self.proposed_markdown_content,
            "status": self.status,
            "author_reviewed_at": self.author_reviewed_at.isoformat() if self.author_reviewed_at else None,
            "author_review_note": self.author_review_note,
            "admin_reviewed_at": self.admin_reviewed_at.isoformat() if self.admin_reviewed_at else None,
            "admin_review_note": self.admin_review_note,
            "merged_version_id": self.merged_version_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
