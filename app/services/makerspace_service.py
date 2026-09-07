from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import re
import secrets

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from flask import current_app
from sqlalchemy import or_

from app.extensions import db
from app.models.makerspace import MakerAudit, MakerDeployment, MakerSession, MakerSpace, MakerWorker, now
from app.models.user import User


class MakerError(Exception):
    def __init__(self, code, status=400):
        self.code, self.status = code, status


SLUG = re.compile(r"[a-z][a-z0-9-]{2,39}\Z")
REPOSITORY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}/[A-Za-z0-9][A-Za-z0-9_.-]{0,99}\Z")
REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9_./-]{0,99}\Z")
SHA = re.compile(r"[0-9a-f]{40}\Z")
RUNTIMES = {"static", "node", "python"}
CATEGORIES = {"tools", "learning", "campus", "games", "other"}
RESERVED = {"new", "mine", "guide", "review", "run", "admin", "api", "settings", "teamup", "users", "favorites"}
QUOTA = {"cpu": 0.5, "memory_mb": 256, "storage_mb": 256, "build_memory_mb": 768, "build_seconds": 300, "build_storage_mb": 1024, "processes": 64}


def hash_value(value):
    return hashlib.sha256(value.encode()).hexdigest()


def aware(value):
    return value.replace(tzinfo=timezone.utc) if value and value.tzinfo is None else value


def crypto():
    key = current_app.config.get("MAKERSPACE_ENCRYPTION_KEY", "")
    if not key:
        raise MakerError("credentials_unavailable", 503)
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt(value):
    return crypto().encrypt(value.encode()).decode()


def decrypt(value):
    return crypto().decrypt(value.encode()).decode() if value else ""


def audit(space, actor, action, **details):
    db.session.add(MakerAudit(space_id=space.id, actor_id=actor.id if actor else None, action=action, details=details))


def active_user(user):
    if not user or user.is_deleted:
        raise MakerError("authentication_required", 401)
    if not user.email_verified:
        raise MakerError("verified_email_required", 403)
    return user


def can_manage(space, user):
    return bool(user and not user.is_deleted and user.email_verified and space.owner_id == user.id)


def administrator(user):
    return bool(user and not user.is_deleted and user.email_verified and user.is_admin())


def reviewing(space, user):
    return administrator(user) and MakerDeployment.query.filter_by(space_id=space.id, review_status="pending").first() is not None


def get_space(slug, user=None, *, manage=False):
    space = MakerSpace.query.filter_by(slug=slug).first()
    if not space or space.status == "archived":
        raise MakerError("not_found", 404)
    if manage and not can_manage(space, user):
        raise MakerError("not_found", 404)
    if not manage and space.status != "published" and not can_manage(space, user) and not reviewing(space, user):
        raise MakerError("not_found", 404)
    return space


def text(data, key, maximum, *, required=True):
    value = data.get(key, "")
    if not isinstance(value, str) or len(value.strip()) > maximum or (required and not value.strip()):
        raise MakerError("invalid_" + key)
    return value.strip()


def validate_settings(value):
    if not isinstance(value, dict) or set(value) - {"runtime", "directory", "build_command", "start_command", "output_directory"}:
        raise MakerError("invalid_settings")
    runtime = value.get("runtime", "static")
    if runtime not in RUNTIMES:
        raise MakerError("invalid_runtime")
    result = {"runtime": runtime}
    for key, default in [("directory", "."), ("output_directory", "dist")]:
        path = value.get(key, default)
        if not isinstance(path, str) or len(path) > 160 or path.startswith("/") or "\\" in path or any(part == ".." for part in path.split("/")) or not re.fullmatch(r"[A-Za-z0-9_./-]+", path):
            raise MakerError("invalid_" + key)
        result[key] = path
    for key in ("build_command", "start_command"):
        command = value.get(key, "")
        if not isinstance(command, str) or len(command) > 1000 or "\x00" in command:
            raise MakerError("invalid_" + key)
        result[key] = command.strip()
    if runtime != "static" and not result["start_command"]:
        raise MakerError("start_command_required")
    return result


def metadata(space):
    return {key: getattr(space, key) for key in ("title_zh", "title_en", "description_zh", "description_en", "category")}


def deployment_payload(deployment, *, private=False):
    value = {key: getattr(deployment, key) for key in ("id", "source_sha", "status", "review_status", "review_note", "error_code", "artifact_digest", "resource_usage", "publication_status")}
    value.update(created_at=deployment.created_at.isoformat(), submitted_at=deployment.submitted_at.isoformat() if deployment.submitted_at else None, finished_at=deployment.finished_at.isoformat() if deployment.finished_at else None)
    if private:
        value.update(snapshot=deployment.snapshot, log=deployment.log)
    return value


def serialize(space, user=None, *, private=False):
    owner = space.owner
    visible = metadata(space) if private else (space.published_metadata or metadata(space))
    value = {"id": space.id, "slug": space.slug, **visible, "kind": space.kind, "status": space.status, "owner": {"id": owner.id, "username": owner.username} if owner and not owner.is_deleted else None, "is_owner": can_manage(space, user), "cover_url": f"/api/makerspace/{space.slug}/cover?v={space.cover_file_id}" if space.cover_file_id else None, "published_deployment_id": space.published_deployment_id, "created_at": space.created_at.isoformat(), "updated_at": space.updated_at.isoformat()}
    if space.kind == "external" and space.status == "published":
        value["launch_path"] = space.external_path
    if private:
        value.update(repository=space.repository, branch=space.branch, settings=space.settings, public_key=space.public_key, auto_deploy=space.auto_deploy, version=space.version, quota=QUOTA)
        value["environment_names"] = sorted(json.loads(decrypt(space.encrypted_environment) or "{}").keys()) if space.encrypted_environment else []
        query = MakerDeployment.query.filter_by(space_id=space.id)
        if not can_manage(space, user):
            query = query.filter_by(review_status="pending")
        deployments = query.order_by(MakerDeployment.created_at.desc()).limit(30).all()
        value["deployments"] = [deployment_payload(item, private=True) for item in deployments]
    return value


def create_space(user, data):
    active_user(user)
    # Serialize per-author quota admission, including simultaneous create calls.
    db.session.query(User).filter_by(id=user.id).with_for_update().one()
    limit = current_app.config.get("MAKERSPACE_MAX_PER_USER", 3)
    if MakerSpace.query.filter(MakerSpace.owner_id == user.id, MakerSpace.status != "archived").count() >= limit:
        raise MakerError("space_limit", 409)
    slug = text(data, "slug", 40)
    if not SLUG.fullmatch(slug) or slug in RESERVED:
        raise MakerError("invalid_slug")
    if MakerSpace.query.filter_by(slug=slug).first():
        raise MakerError("slug_taken", 409)
    space = MakerSpace(owner_id=user.id, slug=slug, settings=validate_settings(data.get("settings", {})))
    update_fields(space, data)
    db.session.add(space)
    db.session.flush()
    audit(space, user, "created")
    return space


def update_fields(space, data):
    for key, maximum in [("title_zh", 100), ("title_en", 100), ("description_zh", 3000), ("description_en", 3000)]:
        setattr(space, key, text(data, key, maximum))
    category = data.get("category", "tools")
    if category not in CATEGORIES:
        raise MakerError("invalid_category")
    space.category = category
    if space.kind == "external":
        return
    repository = data.get("repository", "").strip() if isinstance(data.get("repository", ""), str) else ""
    if repository.startswith("https://github.com/"):
        repository = repository.removeprefix("https://github.com/").rstrip("/")
    if repository.endswith(".git"):
        repository = repository[:-4]
    if repository and not REPOSITORY.fullmatch(repository):
        raise MakerError("invalid_repository")
    branch = data.get("branch", "main")
    if not isinstance(branch, str) or not REF.fullmatch(branch) or ".." in branch or "//" in branch or branch.endswith(("/", ".", ".lock")):
        raise MakerError("invalid_branch")
    space.repository, space.branch = repository, branch
    space.settings = validate_settings(data.get("settings", space.settings or {}))
    if type(data.get("auto_deploy", False)) is not bool:
        raise MakerError("invalid_auto_deploy")
    space.auto_deploy = data.get("auto_deploy", False)


def credentials(space, user):
    if space.kind != "hosted":
        raise MakerError("external_managed_separately", 409)
    key = Ed25519PrivateKey.generate()
    private = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH, serialization.NoEncryption()).decode()
    public = key.public_key().public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH).decode()
    secret = secrets.token_urlsafe(32)
    space.encrypted_private_key = encrypt(private)
    space.public_key = public + f" unikorn-makerspace-{space.id}"
    space.encrypted_webhook_secret = encrypt(secret)
    audit(space, user, "repository_key_rotated")
    return {"public_key": space.public_key, "webhook_secret": secret}


def hosting_ready():
    if not current_app.config.get("MAKERSPACE_HOSTING_ENABLED", False):
        return False
    return MakerWorker.query.filter(MakerWorker.last_seen_at > now() - timedelta(seconds=90)).first() is not None


def queue_deployment(space, user_id, sha=None):
    if space.kind != "hosted":
        raise MakerError("external_managed_separately", 409)
    if space.status in ("suspended", "archived"):
        raise MakerError("space_suspended", 409)
    if not space.repository or not space.encrypted_private_key:
        raise MakerError("connect_repository_first", 409)
    if not hosting_ready():
        raise MakerError("hosting_unavailable", 503)
    if sha is not None and not SHA.fullmatch(sha):
        raise MakerError("invalid_sha")
    if MakerDeployment.query.filter(MakerDeployment.space_id == space.id, MakerDeployment.status.in_(["queued", "building"])).first():
        raise MakerError("deployment_in_progress", 409)
    if MakerDeployment.query.filter(MakerDeployment.space_id == space.id, MakerDeployment.created_at > now() - timedelta(hours=1)).count() >= 6:
        raise MakerError("deployment_rate_limit", 429)
    # Fleet quota is checked again under the worker admission lock before build.
    snapshot = {"metadata": metadata(space), "repository": space.repository, "branch": space.branch, "settings": space.settings, "environment_names": sorted(json.loads(decrypt(space.encrypted_environment) or "{}").keys()), "space_version": space.version}
    item = MakerDeployment(space_id=space.id, requested_by=user_id, source_ref=sha or space.branch, source_sha=sha, snapshot=snapshot, encrypted_environment=space.encrypted_environment)
    db.session.add(item)
    db.session.flush()
    return item


def deployment_for(space, identifier):
    item = db.session.get(MakerDeployment, identifier)
    if not item or item.space_id != space.id:
        raise MakerError("not_found", 404)
    return item


def submit(space, deployment, user):
    if space.status == "suspended":
        raise MakerError("space_suspended", 409)
    if deployment.status != "ready" or not deployment.source_sha or not deployment.artifact_digest:
        raise MakerError("ready_deployment_required", 409)
    if deployment.review_status not in ("draft", "rejected", "withdrawn"):
        raise MakerError("invalid_review_state", 409)
    if deployment.snapshot["space_version"] != space.version:
        raise MakerError("settings_changed_redeploy", 409)
    if MakerDeployment.query.filter_by(space_id=space.id, review_status="pending").first():
        raise MakerError("review_in_progress", 409)
    deployment.review_status, deployment.submitted_at = "pending", now()
    deployment.review_note = None
    audit(space, user, "submitted", deployment_id=deployment.id, source_sha=deployment.source_sha, artifact_digest=deployment.artifact_digest)


def review(space, item, user, decision, note):
    if not administrator(user):
        raise MakerError("admin_required", 403)
    if item.review_status != "pending" or item.status != "ready" or space.status in ("suspended", "archived"):
        raise MakerError("invalid_review_state", 409)
    if user.id == space.owner_id:
        raise MakerError("independent_review_required", 403)
    if decision not in ("approve", "reject") or not isinstance(note, str) or len(note) > 3000:
        raise MakerError("invalid_review")
    if decision == "reject" and not note.strip():
        raise MakerError("review_note_required")
    item.review_status = "approved" if decision == "approve" else "rejected"
    item.reviewed_by, item.reviewed_at, item.review_note = user.id, now(), note.strip()
    if decision == "approve":
        # The worker starts the reviewed artifact with production-only storage.
        # Keep the prior release live until the new runtime passes its check.
        item.publication_status = "queued"
    audit(space, user, "review_" + decision, deployment_id=item.id, source_sha=item.source_sha, artifact_digest=item.artifact_digest, note=note.strip())


def launch(space, user, deployment_id=None):
    if space.status == "suspended":
        raise MakerError("space_suspended", 403)
    if space.kind == "external":
        if space.status != "published" or space.external_path != "/teamup/":
            raise MakerError("not_found", 404)
        return {"url": space.external_path}, None
    identifier = deployment_id or space.published_deployment_id
    if not identifier:
        raise MakerError("ready_deployment_required", 409)
    item = deployment_for(space, identifier)
    private = bool(deployment_id) or item.id != space.published_deployment_id or space.status != "published"
    allowed_reviewer = administrator(user) and item.review_status == "pending"
    if private and not (can_manage(space, user) or allowed_reviewer):
        raise MakerError("not_found", 404)
    port = item.runtime_port if private else item.public_runtime_port
    if item.status != "ready" or not port:
        raise MakerError("runtime_unavailable", 503)
    secret, identifier = secrets.token_urlsafe(32), secrets.token_hex(24)
    if MakerSession.query.filter(MakerSession.expires_at > now()).count() >= 5000:
        raise MakerError("runtime_unavailable", 503)
    if user and MakerSession.query.filter(MakerSession.viewer_id == user.id, MakerSession.expires_at > now()).count() >= 50:
        raise MakerError("session_limit", 429)
    # Every launch has an unguessable resource prefix AND a separate HttpOnly
    # browser secret. Sharing a URL is insufficient to access private content.
    session = MakerSession(id=identifier, cookie_hash=hash_value(secret), space_id=space.id, deployment_id=item.id, viewer_id=user.id if user else None, private=private, expires_at=now() + timedelta(hours=1))
    db.session.add(session)
    return {"url": f"/api/makerspace/run/{identifier}/", "expires_at": session.expires_at.isoformat()}, (identifier, secret)


def runtime_session(identifier, cookie):
    session = db.session.get(MakerSession, identifier)
    if not session or not cookie or aware(session.expires_at) <= now() or not any(hmac.compare_digest(value, hash_value(cookie)) for value in (session.cookie_hash, session.runtime_cookie_hash) if value):
        raise MakerError("preview_expired", 404)
    return authorize_session(session)


def authorize_session(session):
    if aware(session.expires_at) <= now():
        raise MakerError("preview_expired", 404)
    space = db.session.get(MakerSpace, session.space_id)
    item = db.session.get(MakerDeployment, session.deployment_id)
    user = db.session.get(User, session.viewer_id) if session.viewer_id else None
    if not space or space.status in ("suspended", "archived") or not item or item.status != "ready":
        raise MakerError("not_found", 404)
    if session.private:
        if user and user.auth_valid_after and aware(session.created_at) <= aware(user.auth_valid_after):
            raise MakerError("not_found", 404)
        if not (can_manage(space, user) or (administrator(user) and item.review_status == "pending")):
            raise MakerError("not_found", 404)
    elif space.status != "published" or space.published_deployment_id != item.id:
        raise MakerError("not_found", 404)
    return session, item


def bootstrap_session(identifier, token):
    session = MakerSession.query.filter_by(id=identifier).with_for_update().first()
    if not session or not isinstance(token, str) or not session.bootstrap_hash or not session.bootstrap_expires_at or aware(session.bootstrap_expires_at) <= now() or not hmac.compare_digest(session.bootstrap_hash, hash_value(token)):
        raise MakerError("preview_expired", 404)
    authorize_session(session)
    secret = secrets.token_urlsafe(32)
    session.runtime_cookie_hash = hash_value(secret)
    session.bootstrap_hash, session.bootstrap_expires_at = None, None
    db.session.commit()
    return secret


def redact_log(value, secrets_to_hide=()):
    value = str(value)[-24000:]
    for secret in secrets_to_hide:
        if secret:
            value = value.replace(secret, "[redacted]")
    value = re.sub(r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----", "[redacted key]", value, flags=re.S)
    value = re.sub(r"(?i)(authorization\s*[:=]\s*|bearer\s+|(?:password|secret|token|api_key)\s*[:=]\s*)[^\s]+", r"\1[redacted]", value)
    return value
