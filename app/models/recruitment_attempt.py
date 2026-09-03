from datetime import datetime, timezone

from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import JSONB

from app.extensions import db


class RecruitmentAttempt(db.Model):
    """Durable audit record for one NODE recruitment agent run."""

    __tablename__ = "recruitment_attempts"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    username_snapshot = db.Column(db.String(50), nullable=False)
    email_snapshot = db.Column(db.String(100), nullable=False)
    prompt = db.Column(db.Text, nullable=False)
    state = db.Column(db.String(16), nullable=False, default="running")
    success = db.Column(db.Boolean, nullable=False, default=False)
    score = db.Column(db.Integer, nullable=False, default=0)
    tool_calls = db.Column(db.Integer, nullable=False, default=0)
    feedback = db.Column(
        JSON().with_variant(JSONB, "postgresql"),
        nullable=False,
        default=list,
    )
    agent_message = db.Column(db.Text, nullable=False, default="")
    duration_ms = db.Column(db.Integer, nullable=True)
    model = db.Column(db.String(100), nullable=True)
    error = db.Column(db.String(80), nullable=True)
    started_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    completed_at = db.Column(db.DateTime(timezone=True), nullable=True)

    user = db.relationship("User", foreign_keys=[user_id])

    __table_args__ = (
        db.CheckConstraint(
            "state IN ('running', 'complete', 'failed')",
            name="ck_recruitment_attempt_state",
        ),
        db.CheckConstraint(
            "score >= 0 AND score <= 100",
            name="ck_recruitment_attempt_score",
        ),
        db.CheckConstraint(
            "tool_calls >= 0",
            name="ck_recruitment_attempt_tool_calls",
        ),
        db.Index(
            "idx_recruitment_attempt_user_completed",
            "user_id",
            "completed_at",
        ),
        db.Index(
            "idx_recruitment_attempt_state_score",
            "state",
            "score",
        ),
    )

    def to_admin_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "username": (
                self.user.username
                if self.user and not self.user.is_deleted
                else self.username_snapshot
            ),
            "email": self.user.email if self.user and self.user.email else self.email_snapshot,
            "prompt": self.prompt,
            "state": self.state,
            "success": self.success,
            "score": self.score,
            "tool_calls": self.tool_calls,
            "feedback": self.feedback or [],
            "agent_message": self.agent_message,
            "duration_ms": self.duration_ms,
            "model": self.model,
            "error": self.error,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }
