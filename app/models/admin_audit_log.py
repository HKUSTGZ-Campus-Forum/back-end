from datetime import datetime, timezone

from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import JSONB

from app.extensions import db


class AdminAuditLog(db.Model):
    __tablename__ = "admin_audit_logs"

    id = db.Column(db.Integer, primary_key=True)
    actor_user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    action = db.Column(db.String(80), nullable=False, index=True)
    target_type = db.Column(db.String(80), nullable=False, index=True)
    target_id = db.Column(db.Integer, nullable=True, index=True)
    target_label = db.Column(db.String(255), nullable=True)
    note = db.Column(db.Text, nullable=True)
    metadata_json = db.Column("metadata", JSON().with_variant(JSONB, "postgresql"), default=dict, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False, index=True)

    actor = db.relationship("User", foreign_keys=[actor_user_id])

    __table_args__ = (
        db.Index("idx_admin_audit_actor_created", "actor_user_id", "created_at"),
        db.Index("idx_admin_audit_target", "target_type", "target_id"),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "actor_user_id": self.actor_user_id,
            "actor": self.actor.username if self.actor else None,
            "action": self.action,
            "target_type": self.target_type,
            "target_id": self.target_id,
            "target_label": self.target_label,
            "note": self.note,
            "metadata": self.metadata_json or {},
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
