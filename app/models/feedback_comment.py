from datetime import datetime, timezone

from app.extensions import db


class FeedbackComment(db.Model):
    __tablename__ = "feedback_comments"

    VISIBILITY_VISIBLE = "visible"
    VISIBILITY_HIDDEN = "hidden"

    id = db.Column(db.Integer, primary_key=True)
    feedback_id = db.Column(db.Integer, db.ForeignKey("feedbacks.id"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    parent_comment_id = db.Column(db.Integer, db.ForeignKey("feedback_comments.id"), nullable=True)
    content = db.Column(db.Text, nullable=False)
    visibility = db.Column(db.String(20), nullable=False, default=VISIBILITY_VISIBLE, index=True)
    hidden_reason = db.Column(db.Text, nullable=True)
    hidden_by_admin_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    hidden_at = db.Column(db.DateTime(timezone=True), nullable=True)
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
        backref=db.backref("feedback_comments", lazy="dynamic"),
        foreign_keys=[user_id],
    )
    hidden_by_admin = db.relationship(
        "User",
        backref=db.backref("feedback_comments_hidden", lazy="dynamic"),
        foreign_keys=[hidden_by_admin_id],
    )
    replies = db.relationship(
        "FeedbackComment",
        foreign_keys=[parent_comment_id],
        backref=db.backref("parent", remote_side=[id]),
        lazy="dynamic",
    )

    def to_dict(self, viewer_user_id=None, viewer_is_admin=False):
        is_owner = viewer_user_id == self.user_id
        masked_content = self.content
        if self.visibility == self.VISIBILITY_HIDDEN and not (viewer_is_admin or is_owner):
            masked_content = "该评论因管理原因被隐藏"

        return {
            "id": self.id,
            "feedback_id": self.feedback_id,
            "user_id": self.user_id,
            "parent_comment_id": self.parent_comment_id,
            "content": masked_content,
            "visibility": self.visibility,
            "hidden_reason": self.hidden_reason if (viewer_is_admin or is_owner) else None,
            "hidden_by_admin_id": self.hidden_by_admin_id,
            "hidden_at": self.hidden_at.isoformat() if self.hidden_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
