"""Safely make legacy unverified campus-email accounts claimable by SSO.

The command is a dry-run unless ``--apply`` is supplied. Apply is deliberately
gated by an exact database name, candidate count, dry-run plan digest, verified
backup digest, and confirmation phrase. It never fabricates OIDC subjects: the
normal callback stores the real ``(issuer, subject)`` on the user's next login.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any

from sqlalchemy import inspect, text
from sqlalchemy.engine import make_url

from app.extensions import db
from app.models.admin_audit_log import AdminAuditLog
from app.models.oidc_identity import OidcIdentity
from app.models.user import User
from app.scripts.import_scheduler_offerings import create_import_app
from app.services.institutional_email import is_institutional_email, normalize_email


PLAN_VERSION = 1
APPLY_CONFIRMATION = "APPLY_CAMPUS_OIDC_RECOVERY"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
DATABASE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,62}$")


class RecoveryBlocked(RuntimeError):
    """Raised when a reviewed recovery control no longer matches."""


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_value,
    )


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _database_identity() -> dict[str, str]:
    url = make_url(db.engine.url)
    inspector = inspect(db.session.connection())
    return {
        "dialect": db.engine.dialect.name,
        "name": str(url.database or ""),
        "schema": str(inspector.default_schema_name or ""),
    }


def _row_fingerprint(user: User) -> str:
    return _sha256_json({
        "id": user.id,
        "email": normalize_email(user.email),
        "email_verified": bool(user.email_verified),
        "email_verification_code": user.email_verification_code,
        "email_verification_expires_at": user.email_verification_expires_at,
        "auth_valid_after": user.auth_valid_after,
        "is_deleted": bool(user.is_deleted),
        "created_at": user.created_at,
        "updated_at": user.updated_at,
    })


def _candidate_inventory() -> tuple[list[User], list[dict[str, Any]]]:
    grouped: dict[str, list[User]] = {}
    active_users = (
        User.query
        .filter(User.is_deleted.is_(False))
        .order_by(User.created_at.asc(), User.id.asc())
        .all()
    )
    for user in active_users:
        email = normalize_email(user.email)
        if is_institutional_email(email):
            grouped.setdefault(email, []).append(user)

    linked_user_ids = {
        user_id
        for (user_id,) in db.session.query(OidcIdentity.user_id).distinct().all()
    }
    candidates: list[User] = []
    blockers: list[dict[str, Any]] = []
    for users in grouped.values():
        owner = users[0]
        if owner.email_verified or owner.id in linked_user_ids:
            continue
        if len(users) > 1:
            blockers.append({
                "type": "ambiguous_active_email_owner",
                "owner_user_id": owner.id,
                "active_user_ids": [user.id for user in users],
            })
            continue
        candidates.append(owner)

    return candidates, sorted(blockers, key=_canonical_json)


def build_recovery_plan() -> dict[str, Any]:
    candidates, blockers = _candidate_inventory()
    role_counts = Counter(
        user.role.name if user.role else "missing"
        for user in candidates
    )
    now = datetime.now(timezone.utc)
    return {
        "version": PLAN_VERSION,
        "operation": "legacy_oidc_account_recovery",
        "database": _database_identity(),
        "candidate_count": len(candidates),
        "candidate_user_ids": [user.id for user in candidates],
        "candidate_rows": [
            {"user_id": user.id, "row_sha256": _row_fingerprint(user)}
            for user in candidates
        ],
        "role_counts": dict(sorted(role_counts.items())),
        "stored_verification_code_count": sum(
            bool(user.email_verification_code) for user in candidates
        ),
        "live_verification_code_count": sum(
            bool(user.email_verification_code)
            and user.email_verification_expires_at is not None
            and (
                user.email_verification_expires_at.replace(tzinfo=timezone.utc)
                if user.email_verification_expires_at.tzinfo is None
                else user.email_verification_expires_at
            ) > now
            for user in candidates
        ),
        "blockers": blockers,
    }


def recovery_plan_sha256(plan: dict[str, Any]) -> str:
    return _sha256_json(plan)


def _verify_backup(path: str | None, expected_sha256: str | None) -> Path:
    if not expected_sha256 or not SHA256_RE.fullmatch(expected_sha256):
        raise RecoveryBlocked("apply requires a valid verified backup SHA-256")
    if not path:
        raise RecoveryBlocked("apply requires an absolute verified backup path")
    backup = Path(path)
    if not backup.is_absolute() or not backup.is_file() or backup.is_symlink():
        raise RecoveryBlocked("verified backup path is absent or unsafe")

    digest = hashlib.sha256()
    with backup.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    if digest.hexdigest() != expected_sha256:
        raise RecoveryBlocked("verified backup digest does not match the file")
    return backup


def _control_errors(
    plan: dict[str, Any],
    plan_sha256: str,
    *,
    expected_database: str | None,
    expected_plan_sha256: str | None,
    expected_candidates: int | None,
    confirmation: str | None,
) -> list[str]:
    errors = []
    sqlite_memory_test = (
        plan["database"]["dialect"] == "sqlite"
        and expected_database == ":memory:"
    )
    if (
        not sqlite_memory_test
        and (
            not expected_database
            or not DATABASE_NAME_RE.fullmatch(expected_database)
        )
    ):
        errors.append("expected database name is invalid")
    elif plan["database"]["name"] != expected_database:
        errors.append(
            f"database mismatch: {plan['database']['name']!r} != {expected_database!r}"
        )
    if expected_plan_sha256 != plan_sha256:
        errors.append(
            f"plan SHA-256 mismatch: {plan_sha256} != {expected_plan_sha256}"
        )
    if expected_candidates != plan["candidate_count"]:
        errors.append(
            "candidate count mismatch: "
            f"{plan['candidate_count']} != {expected_candidates}"
        )
    if confirmation != APPLY_CONFIRMATION:
        errors.append(f"apply requires confirmation {APPLY_CONFIRMATION!r}")
    return errors


def _lock_recovery_tables() -> None:
    if db.engine.dialect.name == "postgresql":
        db.session.execute(text(
            "LOCK TABLE users, user_oidc_identities, admin_audit_logs "
            "IN SHARE ROW EXCLUSIVE MODE"
        ))


def run_recovery(
    *,
    apply: bool = False,
    expected_database: str | None = None,
    expected_plan_sha256: str | None = None,
    expected_candidates: int | None = None,
    backup_path: str | None = None,
    backup_sha256: str | None = None,
    confirmation: str | None = None,
    cutoff: datetime | None = None,
    verify_backup: bool = True,
) -> dict[str, Any]:
    try:
        plan = build_recovery_plan()
        plan_sha256 = recovery_plan_sha256(plan)
        result = {
            "status": "blocked" if plan["blockers"] else "dry-run",
            "mode": "apply" if apply else "dry-run",
            "plan_sha256": plan_sha256,
            "plan": plan,
        }
        if not apply:
            db.session.rollback()
            return result

        controls = _control_errors(
            plan,
            plan_sha256,
            expected_database=expected_database,
            expected_plan_sha256=expected_plan_sha256,
            expected_candidates=expected_candidates,
            confirmation=confirmation,
        )
        if plan["blockers"] or controls:
            result["status"] = "blocked"
            result["control_errors"] = controls
            db.session.rollback()
            return result

        if verify_backup:
            _verify_backup(backup_path, backup_sha256)

        _lock_recovery_tables()
        db.session.expire_all()
        locked_plan = build_recovery_plan()
        locked_sha256 = recovery_plan_sha256(locked_plan)
        if locked_sha256 != plan_sha256:
            result["status"] = "blocked"
            result["control_errors"] = [
                "recovery plan changed while acquiring apply locks: "
                f"before={plan_sha256} locked={locked_sha256}"
            ]
            result["locked_plan_sha256"] = locked_sha256
            db.session.rollback()
            return result

        recovery_cutoff = cutoff or datetime.now(timezone.utc)
        if recovery_cutoff.tzinfo is None:
            recovery_cutoff = recovery_cutoff.replace(tzinfo=timezone.utc)
        recovery_cutoff = recovery_cutoff.astimezone(timezone.utc).replace(microsecond=0)

        candidate_ids = locked_plan["candidate_user_ids"]
        candidates = (
            User.query
            .filter(User.id.in_(candidate_ids))
            .order_by(User.id.asc())
            .all()
        ) if candidate_ids else []
        if len(candidates) != len(candidate_ids):
            raise RecoveryBlocked("one or more candidate users disappeared during apply")

        for user in candidates:
            user.email = normalize_email(user.email)
            user.email_verified = True
            user.email_verification_code = None
            user.email_verification_expires_at = None
            user.auth_valid_after = recovery_cutoff

        db.session.add(AdminAuditLog(
            actor_user_id=None,
            action="legacy_oidc_account_recovery",
            target_type="user_batch",
            target_id=None,
            target_label=recovery_cutoff.isoformat(),
            note=(
                "Made reviewed legacy campus-email accounts claimable by SSO; "
                "OIDC subjects remain provider-issued at first login."
            ),
            metadata_json={
                "plan_sha256": locked_sha256,
                "candidate_count": len(candidate_ids),
                "candidate_user_ids": candidate_ids,
                "auth_valid_after": recovery_cutoff.isoformat(),
                "backup_sha256": backup_sha256,
            },
        ))
        db.session.flush()
        db.session.expire_all()
        remaining_plan = build_recovery_plan()
        if remaining_plan["candidate_count"] or remaining_plan["blockers"]:
            raise RecoveryBlocked("post-apply verification found remaining candidates")
        db.session.commit()
        result["status"] = "applied"
        result["applied"] = {
            "candidate_count": len(candidate_ids),
            "candidate_user_ids": candidate_ids,
            "auth_valid_after": recovery_cutoff.isoformat(),
        }
        return result
    except Exception:
        db.session.rollback()
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Make reviewed legacy campus-email accounts claimable by SSO.",
    )
    parser.add_argument("--database-url", help="Optional database URL; otherwise DATABASE_URL is used.")
    parser.add_argument("--apply", action="store_true", help="Apply the reviewed dry-run plan.")
    parser.add_argument("--expected-database")
    parser.add_argument("--expected-plan-sha256")
    parser.add_argument("--expected-candidates", type=int)
    parser.add_argument("--backup-path")
    parser.add_argument("--backup-sha256")
    parser.add_argument("--confirmation")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    app = create_import_app(args.database_url)
    try:
        with app.app_context():
            result = run_recovery(
                apply=args.apply,
                expected_database=args.expected_database,
                expected_plan_sha256=args.expected_plan_sha256,
                expected_candidates=args.expected_candidates,
                backup_path=args.backup_path,
                backup_sha256=args.backup_sha256,
                confirmation=args.confirmation,
            )
    except Exception as exc:
        print(json.dumps({
            "status": "failed",
            "message": str(exc),
        }, ensure_ascii=False, sort_keys=True), file=sys.stderr)
        raise SystemExit(1) from exc

    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    if result["status"] == "blocked":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
