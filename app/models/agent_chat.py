from datetime import datetime, timezone
import uuid

from app.extensions import db


def utcnow():
    return datetime.now(timezone.utc)


class AgentConversation(db.Model):
    __tablename__ = "agent_conversations"

    id = db.Column(db.Integer, primary_key=True)
    public_id = db.Column(
        db.String(36),
        nullable=False,
        unique=True,
        default=lambda: str(uuid.uuid4()),
    )
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    title = db.Column(db.String(80), nullable=False, default="New conversation")
    is_deleted = db.Column(db.Boolean, nullable=False, default=False)
    deleted_at = db.Column(db.DateTime(timezone=True), nullable=True)
    last_message_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=utcnow,
    )
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=utcnow,
    )
    updated_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=utcnow,
        onupdate=utcnow,
    )

    user = db.relationship(
        "User",
        backref=db.backref(
            "agent_conversations",
            lazy="dynamic",
            cascade="all, delete-orphan",
        ),
    )
    messages = db.relationship(
        "AgentMessage",
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="AgentMessage.id",
    )

    __table_args__ = (
        db.Index(
            "idx_agent_conversations_user_activity",
            "user_id",
            "is_deleted",
            "last_message_at",
        ),
    )


class AgentMessage(db.Model):
    __tablename__ = "agent_messages"

    ROLE_USER = "user"
    ROLE_ASSISTANT = "assistant"
    ROLES = {ROLE_USER, ROLE_ASSISTANT}

    id = db.Column(db.Integer, primary_key=True)
    conversation_id = db.Column(
        db.Integer,
        db.ForeignKey("agent_conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    role = db.Column(db.String(16), nullable=False)
    content = db.Column(db.Text, nullable=False)
    input_tokens = db.Column(db.Integer, nullable=True)
    output_tokens = db.Column(db.Integer, nullable=True)
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=utcnow,
    )

    conversation = db.relationship("AgentConversation", back_populates="messages")

    __table_args__ = (
        db.CheckConstraint(
            "role IN ('user', 'assistant')",
            name="valid_agent_message_role",
        ),
        db.Index(
            "idx_agent_messages_conversation_order",
            "conversation_id",
            "id",
        ),
    )
