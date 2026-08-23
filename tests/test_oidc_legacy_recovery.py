from datetime import datetime, timedelta, timezone

import pytest

from app import create_app
from app.extensions import db
from app.models.admin_audit_log import AdminAuditLog
from app.models.oidc_identity import OidcIdentity
from app.models.user import User
from app.models.user_role import UserRole
from app.routes.auth import check_if_token_revoked
from app.scripts.recover_legacy_oidc_accounts import (
    APPLY_CONFIRMATION,
    recovery_plan_sha256,
    run_recovery,
)


class RecoveryTestConfig:
    TESTING = True
    SECRET_KEY = "recovery-test-session-secret"
    JWT_SECRET_KEY = "recovery-test-jwt-secret-with-at-least-32-bytes"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_ENGINE_OPTIONS = {}
    CACHE_TYPE = "SimpleCache"
    AUTO_INIT_ON_STARTUP = False
    ENABLE_BACKGROUND_TASKS = False
    CAMPUS_SSO_ISSUER = "https://sso.hkust-gz.edu.cn"


@pytest.fixture
def app():
    application = create_app(RecoveryTestConfig)
    with application.app_context():
        db.create_all()
        db.session.add(UserRole(name=UserRole.USER))
        db.session.commit()
    return application


def _user(role, username, email, *, verified=False, code=None):
    user = User(
        username=username,
        email=email,
        email_verified=verified,
        role_id=role.id,
        email_verification_code=code,
        email_verification_expires_at=(
            datetime.now(timezone.utc) - timedelta(days=1) if code else None
        ),
    )
    user.set_password("unused-password")
    db.session.add(user)
    db.session.flush()
    return user


def test_recovery_updates_only_unambiguous_unlinked_campus_accounts(app):
    with app.app_context():
        role = UserRole.query.filter_by(name=UserRole.USER).one()
        candidate = _user(
            role,
            "legacy_candidate",
            " Legacy@Connect.HKUST-GZ.edu.cn ",
            code="123456",
        )
        verified = _user(
            role,
            "already_verified",
            "verified@connect.hkust-gz.edu.cn",
            verified=True,
        )
        external = _user(role, "external", "external@example.com")
        linked = _user(role, "already_linked", "linked@hkust-gz.edu.cn")
        db.session.add(OidcIdentity(
            user_id=linked.id,
            issuer=RecoveryTestConfig.CAMPUS_SSO_ISSUER,
            subject="linked-subject",
        ))
        db.session.commit()

        dry_run = run_recovery()
        assert dry_run["status"] == "dry-run"
        assert dry_run["plan"]["candidate_user_ids"] == [candidate.id]
        assert dry_run["plan"]["stored_verification_code_count"] == 1
        assert dry_run["plan"]["live_verification_code_count"] == 0

        cutoff = datetime(2026, 8, 23, 12, 0, 0, tzinfo=timezone.utc)
        applied = run_recovery(
            apply=True,
            expected_database=":memory:",
            expected_plan_sha256=recovery_plan_sha256(dry_run["plan"]),
            expected_candidates=1,
            backup_sha256="a" * 64,
            confirmation=APPLY_CONFIRMATION,
            cutoff=cutoff,
            verify_backup=False,
        )

        assert applied["status"] == "applied", applied
        db.session.refresh(candidate)
        db.session.refresh(verified)
        db.session.refresh(external)
        db.session.refresh(linked)
        assert candidate.email == "legacy@connect.hkust-gz.edu.cn"
        assert candidate.email_verified is True
        assert candidate.email_verification_code is None
        assert candidate.email_verification_expires_at is None
        assert candidate.auth_valid_after == cutoff.replace(tzinfo=None)
        assert verified.auth_valid_after is None
        assert external.auth_valid_after is None
        assert linked.auth_valid_after is None

        audit = AdminAuditLog.query.filter_by(
            action="legacy_oidc_account_recovery"
        ).one()
        assert audit.metadata_json["candidate_user_ids"] == [candidate.id]
        assert audit.metadata_json["backup_sha256"] == "a" * 64

        repeated = run_recovery()
        assert repeated["status"] == "dry-run"
        assert repeated["plan"]["candidate_count"] == 0


def test_recovery_blocks_ambiguous_active_email_owners(app):
    with app.app_context():
        role = UserRole.query.filter_by(name=UserRole.USER).one()
        first = _user(role, "first_owner", "duplicate@hkust-gz.edu.cn")
        second = _user(role, "second_owner", " DUPLICATE@hkust-gz.edu.cn ")
        db.session.commit()

        result = run_recovery()

        assert result["status"] == "blocked"
        assert result["plan"]["candidate_count"] == 0
        assert result["plan"]["blockers"] == [{
            "type": "ambiguous_active_email_owner",
            "owner_user_id": first.id,
            "active_user_ids": [first.id, second.id],
        }]


def test_account_cutoff_rejects_only_tokens_issued_before_it(app):
    with app.app_context():
        role = UserRole.query.filter_by(name=UserRole.USER).one()
        user = _user(role, "cutoff_user", "cutoff@hkust-gz.edu.cn", verified=True)
        cutoff = datetime(2026, 8, 23, 12, 0, 0, tzinfo=timezone.utc)
        user.auth_valid_after = cutoff
        db.session.commit()

        assert check_if_token_revoked({}, {
            "jti": "old-token",
            "sub": str(user.id),
            "iat": int(cutoff.timestamp()) - 1,
        }) is True
        assert check_if_token_revoked({}, {
            "jti": "replacement-token",
            "sub": str(user.id),
            "iat": int(cutoff.timestamp()),
        }) is False
        assert check_if_token_revoked({}, {
            "jti": "missing-issue-time",
            "sub": str(user.id),
        }) is True
