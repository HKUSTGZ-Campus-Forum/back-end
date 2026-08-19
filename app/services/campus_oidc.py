"""HKUST(GZ) Campus SSO client and local-account reconciliation helpers."""

from __future__ import annotations

import hashlib
import re
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from flask import current_app
from sqlalchemy.exc import IntegrityError

from app.extensions import db, oauth
from app.models.oidc_identity import OidcIdentity, OidcLoginTicket
from app.models.user import User
from app.models.user_role import UserRole
from app.services.institutional_email import (
    acquire_email_transaction_lock,
    active_email_owner,
    is_institutional_email,
    normalize_email,
)


class CampusOidcError(Exception):
    """Expected OIDC reconciliation failure with a stable public error code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def register_campus_oidc_client(app) -> None:
    """Register the configured provider without performing network I/O."""

    oauth.register(
        "campus_sso",
        client_id=app.config.get("CAMPUS_SSO_CLIENT_ID") or None,
        client_secret=app.config.get("CAMPUS_SSO_CLIENT_SECRET") or None,
        server_metadata_url=app.config.get(
            "CAMPUS_SSO_METADATA_URL",
            "https://devsso.hkust-gz.edu.cn/.well-known/openid-configuration",
        ),
        client_kwargs={
            "scope": app.config.get("CAMPUS_SSO_SCOPES", "openid profile"),
            "code_challenge_method": "S256",
            "token_endpoint_auth_method": "client_secret_basic",
        },
    )


def campus_oidc_is_configured() -> bool:
    return bool(
        current_app.config.get("CAMPUS_SSO_ENABLED")
        and current_app.config.get("CAMPUS_SSO_CLIENT_ID")
        and current_app.config.get("CAMPUS_SSO_CLIENT_SECRET")
        and current_app.config.get("CAMPUS_SSO_REDIRECT_URI")
    )


def get_campus_oidc_client():
    return oauth.create_client("campus_sso")


def sanitize_return_to(value: object, default: str = "/") -> str:
    """Accept only bounded same-origin paths for post-login navigation."""

    if not isinstance(value, str):
        return default
    candidate = value.strip()
    if (
        not candidate.startswith("/")
        or candidate.startswith("//")
        or "\\" in candidate
        or len(candidate) > 512
    ):
        return default
    return candidate


def _clean_claim(value: object, max_length: int) -> str | None:
    if value is None:
        return None
    result = str(value).strip()
    if not result:
        return None
    return result[:max_length]


def _username_seed(claims: dict, email: str) -> str:
    raw = (
        claims.get("preferred_username")
        or claims.get("name")
        or email.partition("@")[0]
        or "unikorn_user"
    )
    normalized = re.sub(r"[^A-Za-z0-9_]", "_", str(raw)).strip("_")
    normalized = re.sub(r"_+", "_", normalized)[:40]
    return normalized if len(normalized) >= 3 else "unikorn_user"


def _available_username(seed: str) -> str:
    candidate = seed[:50]
    suffix = 1
    while User.query.filter_by(username=candidate).first() is not None:
        suffix += 1
        suffix_text = f"_{suffix}"
        candidate = f"{seed[:50 - len(suffix_text)]}{suffix_text}"
    return candidate


def _default_user_role() -> UserRole:
    role = UserRole.query.filter_by(name=UserRole.USER).first()
    if role is None:
        raise CampusOidcError(
            "account_unavailable",
            "The default local user role is not configured.",
        )
    return role


def reconcile_oidc_user(claims: dict) -> User:
    """Resolve a verified OIDC principal to exactly one active local user.

    Existing accounts are linked only when the institutional email has already
    been verified locally. This prevents an unverified, pre-registered address
    from becoming a bridge into a school user's account.
    """

    issuer = current_app.config["CAMPUS_SSO_ISSUER"]
    subject = _clean_claim(claims.get("sub"), 255)
    if not subject:
        raise CampusOidcError("invalid_response", "OIDC subject is missing.")

    email = normalize_email(claims.get("email"))
    if not is_institutional_email(email):
        raise CampusOidcError(
            "institutional_email_required",
            "SSO did not provide an eligible HKUST(GZ) email address.",
        )

    identity = OidcIdentity.query.filter_by(
        issuer=issuer,
        subject=subject,
    ).first()
    if identity is not None:
        if identity.user is None or identity.user.is_deleted:
            raise CampusOidcError(
                "account_unavailable",
                "The linked UniKorn account is unavailable.",
            )
        _update_identity_snapshot(identity, claims, email)
        identity.user.last_active_at = datetime.now(timezone.utc)
        db.session.commit()
        return identity.user

    acquire_email_transaction_lock(email)

    # Re-check after taking the per-email lock to close the first-login race.
    identity = OidcIdentity.query.filter_by(
        issuer=issuer,
        subject=subject,
    ).first()
    if identity is not None:
        if identity.user is None or identity.user.is_deleted:
            raise CampusOidcError(
                "account_unavailable",
                "The linked UniKorn account is unavailable.",
            )
        _update_identity_snapshot(identity, claims, email)
        identity.user.last_active_at = datetime.now(timezone.utc)
        db.session.commit()
        return identity.user

    user = active_email_owner(email)
    if user is not None and not user.email_verified:
        raise CampusOidcError(
            "account_conflict",
            "An unverified UniKorn account already uses this email address.",
        )

    if user is None:
        role = _default_user_role()
        user = User(
            username=_available_username(_username_seed(claims, email)),
            email=email,
            email_verified=True,
            role_id=role.id,
        )
        # Preserve the existing non-null password schema while ensuring an SSO
        # provisioned account has no known local password.
        user.set_password(secrets.token_urlsafe(64))
        db.session.add(user)
        db.session.flush()

    identity = OidcIdentity(
        user_id=user.id,
        issuer=issuer,
        subject=subject,
    )
    _update_identity_snapshot(identity, claims, email)
    user.last_active_at = datetime.now(timezone.utc)
    db.session.add(identity)

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        identity = OidcIdentity.query.filter_by(
            issuer=issuer,
            subject=subject,
        ).first()
        if identity is None or identity.user is None or identity.user.is_deleted:
            raise CampusOidcError(
                "account_unavailable",
                "The SSO identity could not be linked safely.",
            )
        return identity.user

    return user


def _update_identity_snapshot(
    identity: OidcIdentity,
    claims: dict,
    email: str,
) -> None:
    identity.last_seen_email = email
    identity.display_name = _clean_claim(
        claims.get("display_name") or claims.get("name"),
        200,
    )
    identity.account_type = _clean_claim(claims.get("type"), 50)
    identity.department = _clean_claim(
        claims.get("department") or claims.get("depart"),
        100,
    )
    identity.employee_id = _clean_claim(claims.get("emp_id"), 100)
    identity.last_login_at = datetime.now(timezone.utc)


def issue_login_ticket(user: User, return_to: str) -> str:
    raw_code = secrets.token_urlsafe(32)
    code_hash = hashlib.sha256(raw_code.encode("utf-8")).hexdigest()
    ttl = max(
        30,
        min(
            int(current_app.config["CAMPUS_SSO_LOGIN_TICKET_TTL_SECONDS"]),
            600,
        ),
    )
    db.session.add(OidcLoginTicket(
        code_hash=code_hash,
        user_id=user.id,
        return_to=sanitize_return_to(return_to),
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=ttl),
    ))
    db.session.commit()
    return raw_code


def consume_login_ticket(raw_code: object) -> OidcLoginTicket | None:
    if not isinstance(raw_code, str) or not 20 <= len(raw_code) <= 200:
        return None

    code_hash = hashlib.sha256(raw_code.encode("utf-8")).hexdigest()
    ticket = OidcLoginTicket.query.filter_by(code_hash=code_hash).first()
    if ticket is None or ticket.consumed_at is not None:
        return None

    now = datetime.now(timezone.utc)
    expires_at = ticket.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= now:
        return None

    updated = OidcLoginTicket.query.filter(
        OidcLoginTicket.id == ticket.id,
        OidcLoginTicket.consumed_at.is_(None),
        OidcLoginTicket.expires_at > now,
    ).update(
        {OidcLoginTicket.consumed_at: now},
        synchronize_session=False,
    )
    if updated != 1:
        db.session.rollback()
        return None

    db.session.commit()
    return db.session.get(OidcLoginTicket, ticket.id)


def build_oidc_logout_url(id_token: str) -> str | None:
    endpoint = current_app.config.get("CAMPUS_SSO_END_SESSION_ENDPOINT")
    post_logout_uri = current_app.config.get(
        "CAMPUS_SSO_POST_LOGOUT_REDIRECT_URI"
    )
    if not endpoint or not post_logout_uri or not id_token:
        return None
    query = urlencode({
        'id_token_hint': id_token,
        'post_logout_redirect_uri': post_logout_uri,
    })
    return f"{endpoint}?{query}"
