"""Persistent identity and one-time login records for campus OIDC SSO."""

from datetime import datetime, timezone

from app.extensions import db


def utcnow():
    return datetime.now(timezone.utc)


class OidcIdentity(db.Model):
    """Bind one verified OIDC principal to one local UniKorn account."""

    __tablename__ = "user_oidc_identities"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    issuer = db.Column(db.String(255), nullable=False)
    subject = db.Column(db.String(255), nullable=False)
    last_seen_email = db.Column(db.String(100), nullable=True)
    display_name = db.Column(db.String(200), nullable=True)
    account_type = db.Column(db.String(50), nullable=True)
    department = db.Column(db.String(100), nullable=True)
    employee_id = db.Column(db.String(100), nullable=True)
    created_at = db.Column(
        db.DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )
    updated_at = db.Column(
        db.DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
        nullable=False,
    )
    last_login_at = db.Column(
        db.DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )

    user = db.relationship(
        "User",
        backref=db.backref(
            "oidc_identities",
            lazy="dynamic",
            cascade="all, delete-orphan",
        ),
    )

    __table_args__ = (
        db.UniqueConstraint(
            "issuer",
            "subject",
            name="uq_user_oidc_identities_issuer_subject",
        ),
    )


class OidcLoginTicket(db.Model):
    """Single-use, short-lived bridge from the OIDC callback to the SPA."""

    __tablename__ = "oidc_login_tickets"

    id = db.Column(db.Integer, primary_key=True)
    code_hash = db.Column(db.String(64), unique=True, nullable=False)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    return_to = db.Column(db.String(512), nullable=False, default="/")
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False, index=True)
    consumed_at = db.Column(db.DateTime(timezone=True), nullable=True)
    created_at = db.Column(
        db.DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )

    user = db.relationship("User")
