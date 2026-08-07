"""Institutional email normalization and active-account ownership helpers."""

from __future__ import annotations

import hashlib
import re

from sqlalchemy import func, text

from app.extensions import db
from app.models.user import User


INSTITUTIONAL_EMAIL_DOMAINS = frozenset({
    "connect.hkust-gz.edu.cn",
    "hkust-gz.edu.cn",
})

_EMAIL_PATTERN = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")


def normalize_email(email: object) -> str:
    """Return the application's canonical representation for an email address."""
    if not isinstance(email, str):
        return ""
    return email.strip().lower()


def is_institutional_email(email: object) -> bool:
    """Return whether *email* is a syntactically valid HKUST-GZ address."""
    normalized = normalize_email(email)
    if not normalized or not _EMAIL_PATTERN.fullmatch(normalized):
        return False

    local_part, separator, domain = normalized.rpartition("@")
    return bool(local_part and separator and domain in INSTITUTIONAL_EMAIL_DOMAINS)


def acquire_email_transaction_lock(email: object) -> None:
    """Serialize ownership decisions for one email on PostgreSQL.

    SQLite is used by the test suite and does not provide transaction advisory
    locks, so the helper intentionally becomes a no-op there.
    """
    normalized = normalize_email(email)
    if not normalized:
        return

    bind = db.session.get_bind()
    if bind.dialect.name != "postgresql":
        return

    digest = hashlib.blake2b(normalized.encode("utf-8"), digest_size=8).digest()
    lock_key = int.from_bytes(digest, byteorder="big", signed=True)
    db.session.execute(
        text("SELECT pg_advisory_xact_lock(:lock_key)"),
        {"lock_key": lock_key},
    )


def active_users_with_email(email: object):
    """Build a deterministic query for active users sharing an email principal."""
    normalized = normalize_email(email)
    return User.query.filter(
        User.is_deleted.is_(False),
        func.lower(func.trim(User.email)) == normalized,
    ).order_by(User.created_at.asc(), User.id.asc())


def active_email_owner(email: object, *, exclude_user_id: int | None = None):
    """Return the oldest active account using a normalized email, if any."""
    query = active_users_with_email(email)
    if exclude_user_id is not None:
        query = query.filter(User.id != exclude_user_id)
    return query.first()


def canonical_email_account(email: object):
    """Return the oldest active account allowed to claim a legacy duplicate."""
    return active_users_with_email(email).first()


def canonical_verified_email_account(email: object):
    """Return the canonical verified institutional principal for an email."""
    normalized = normalize_email(email)
    if not is_institutional_email(normalized):
        return None
    return active_users_with_email(normalized).filter(User.email_verified.is_(True)).first()


# Explicit alias for callers that benefit from the more descriptive name.
normalize_institutional_email = normalize_email
