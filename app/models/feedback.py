from datetime import datetime, timezone

from app.extensions import db


class Feedback(db.Model):
    __tablename__ = "feedbacks"

    STATUS_PENDING_REVIEW = "pending_review"
    STATUS_REJECTED = "rejected"
    STATUS_PUBLISHED = "published"
    STATUS_CLOSED = "closed"

    id = db.Column(db.Integer, primary_key=True)
    author_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    title = db.Column(db.String(200), nullable=False)
    status = db.Column(db.String(40), nullable=False, default=STATUS_PENDING_REVIEW, index=True)
    current_version_id = db.Column(db.Integer, db.ForeignKey("feedback_versions.id"), nullable=True)
    comments_ended = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    published_at = db.Column(db.DateTime(timezone=True), nullable=True)
    rejected_at = db.Column(db.DateTime(timezone=True), nullable=True)
    closed_at = db.Column(db.DateTime(timezone=True), nullable=True)
    updated_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    author = db.relationship(
        "User",
        backref=db.backref("feedbacks", lazy="dynamic"),
        foreign_keys=[author_id],
    )
    current_version = db.relationship(
        "FeedbackVersion",
        foreign_keys=[current_version_id],
        uselist=False,
        post_update=True,
    )
    versions = db.relationship(
        "FeedbackVersion",
        foreign_keys="FeedbackVersion.feedback_id",
        backref=db.backref("feedback", lazy="joined"),
        lazy="dynamic",
        cascade="all, delete-orphan",
    )
    merge_requests = db.relationship(
        "FeedbackMergeRequest",
        foreign_keys="FeedbackMergeRequest.feedback_id",
        backref=db.backref("feedback", lazy="joined"),
        lazy="dynamic",
        cascade="all, delete-orphan",
    )
    comments = db.relationship(
        "FeedbackComment",
        foreign_keys="FeedbackComment.feedback_id",
        backref=db.backref("feedback", lazy="joined"),
        lazy="dynamic",
        cascade="all, delete-orphan",
    )
    audit_events = db.relationship(
        "FeedbackAuditEvent",
        foreign_keys="FeedbackAuditEvent.feedback_id",
        backref=db.backref("feedback", lazy="joined"),
        lazy="dynamic",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        db.Index("idx_feedbacks_status_updated", "status", "updated_at"),
    )

    def to_dict(self, include_private=False):
        data = {
            "id": self.id,
            "author_id": self.author_id,
            "title": self.title,
            "status": self.status,
            "current_version_id": self.current_version_id,
            "comments_ended": self.comments_ended,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "published_at": self.published_at.isoformat() if self.published_at else None,
            "closed_at": self.closed_at.isoformat() if self.closed_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

        if self.author:
            data["author"] = self.author.username
            data["author_avatar"] = self.author.avatar_url

        if self.current_version:
            data["current_version"] = self.current_version.to_dict()

        if include_private:
            data["rejected_at"] = self.rejected_at.isoformat() if self.rejected_at else None

        return data
