from __future__ import annotations

from datetime import timedelta
from functools import wraps
import hashlib
import hmac
import json
import re
import secrets
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request, send_file
from flask_jwt_extended import jwt_required
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models.makerspace import MakerAudit, MakerDeployment, MakerSession, MakerSpace, MakerWebhookDelivery, MakerWorker, now
from app.models.user import User
from app.services import makerspace_service as service
from app.services import makerspace_social as social
from app.utils.permissions import get_authenticated_user


bp = Blueprint("makerspace", __name__, url_prefix="/makerspace")


@bp.errorhandler(service.MakerError)
def maker_error(error):
    db.session.rollback()
    return jsonify({"error": error.code, "code": error.code}), error.status


@bp.errorhandler(IntegrityError)
def conflicting_write(_error):
    db.session.rollback()
    return jsonify({"error": "conflicting_update", "code": "conflicting_update"}), 409


@bp.after_request
def private_response(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    if request.path.startswith('/makerspace/run/'):
        response.headers.setdefault('Content-Security-Policy', "sandbox allow-scripts allow-forms allow-downloads; default-src 'none'; frame-ancestors 'self'")
    return response


def body():
    if len(request.get_data(cache=True)) > 65536:
        raise service.MakerError("request_too_large", 413)
    value = request.get_json(silent=True)
    if not isinstance(value, dict):
        raise service.MakerError("invalid_request")
    if request.content_length and request.content_length > 65536:
        raise service.MakerError("request_too_large", 413)
    return value


def owner_space(slug):
    user = service.active_user(get_authenticated_user())
    # One row lock coordinates edit/deploy/submit/review on every code path.
    db.session.query(MakerSpace).filter_by(slug=slug).with_for_update().first()
    return service.get_space(slug, user, manage=True), user


def editable(space):
    if MakerDeployment.query.filter(MakerDeployment.space_id == space.id, (MakerDeployment.status.in_(["queued", "building"])) | (MakerDeployment.review_status == "pending") | (MakerDeployment.publication_status.in_(["queued", "publishing"])) ).first():
        raise service.MakerError("withdraw_or_wait_before_edit", 409)


@bp.get("/capabilities")
def capabilities():
    return jsonify({"hosting_ready": service.hosting_ready(), "credentials_ready": bool(current_app.config.get("MAKERSPACE_ENCRYPTION_KEY")), "max_spaces": current_app.config.get("MAKERSPACE_MAX_PER_USER", 3), "quota": service.QUOTA, "runtimes": sorted(service.RUNTIMES)})


@bp.get("")
@jwt_required(optional=True)
def catalog():
    query = MakerSpace.query.filter_by(status="published")
    # Search published metadata only: draft edits must never leak through search.
    items = query.order_by(MakerSpace.updated_at.desc()).limit(500).all()
    user = get_authenticated_user()
    values = social.decorate([service.serialize(space, user) for space in items], user)
    search = request.args.get("q", "")[:100].casefold()
    category = request.args.get("category", "")
    if search:
        values = [v for v in values if search in " ".join(v[k] for k in ("title_zh", "title_en", "description_zh", "description_en")).casefold()]
    if category:
        values = [v for v in values if v["category"] == category]
    return jsonify({"spaces": values})


@bp.get("/mine")
@jwt_required()
def mine():
    user = service.active_user(get_authenticated_user())
    items = MakerSpace.query.filter(MakerSpace.owner_id == user.id, MakerSpace.status != "archived").order_by(MakerSpace.updated_at.desc()).all()
    return jsonify({"spaces": social.decorate([service.serialize(item, user, private=True) for item in items], user)})


@bp.post("")
@jwt_required()
def create():
    user = service.active_user(get_authenticated_user())
    space = service.create_space(user, body())
    db.session.commit()
    return jsonify(service.serialize(space, user, private=True)), 201


@bp.get("/<slug>")
@jwt_required(optional=True)
def detail(slug):
    user = get_authenticated_user()
    space = service.get_space(slug, user)
    private = service.can_manage(space, user) or service.reviewing(space, user)
    return jsonify(social.decorate([service.serialize(space, user, private=private)], user)[0])


@bp.get("/users/<int:user_id>")
@jwt_required(optional=True)
def creator_spaces(user_id):
    owner = User.query.filter_by(id=user_id, is_deleted=False).first()
    if not owner:
        raise service.MakerError("not_found", 404)
    viewer = get_authenticated_user()
    own = bool(viewer and viewer.id == user_id and not viewer.is_deleted and viewer.email_verified)
    query = MakerSpace.query.filter_by(owner_id=user_id)
    query = query.filter(MakerSpace.status != "archived") if own else query.filter_by(status="published")
    items = query.order_by(MakerSpace.updated_at.desc()).limit(500).all()
    # Other users see only reviewed public metadata; private settings never enter profiles.
    values = [service.serialize(item, viewer) for item in items]
    if own:
        for value, item in zip(values, items):
            value.update(service.metadata(item))
    return jsonify({"spaces": social.decorate(values, viewer)})


@bp.get("/favorites")
@jwt_required()
def favorite_spaces():
    user = service.active_user(get_authenticated_user())
    from app.models.makerspace import MakerFavorite
    items = (MakerSpace.query.join(MakerFavorite, MakerFavorite.space_id == MakerSpace.id)
             .filter(MakerFavorite.user_id == user.id, MakerSpace.status == "published")
             .order_by(MakerFavorite.created_at.desc()).limit(500).all())
    return jsonify({"spaces": social.decorate([service.serialize(item, user) for item in items], user)})


@bp.route("/<slug>/likes", methods=["PUT", "DELETE"])
@bp.route("/<slug>/favorites", methods=["PUT", "DELETE"])
@jwt_required()
def space_reaction(slug):
    user = service.active_user(get_authenticated_user())
    from app.models.makerspace import MakerLike, MakerFavorite
    model = MakerLike if request.path.endswith('/likes') else MakerFavorite
    space = MakerSpace.query.filter_by(slug=slug, status="published").with_for_update().first()
    if not space:
        raise service.MakerError("not_found", 404)
    existing = db.session.get(model, (space.id, user.id))
    if request.method == "PUT" and not existing:
        db.session.add(model(space_id=space.id, user_id=user.id))
    elif request.method == "DELETE" and existing:
        db.session.delete(existing)
    db.session.commit()
    return jsonify(social.decorate([{"id": space.id}], user)[0])


@bp.put("/<slug>/cover")
@jwt_required()
def set_cover(slug):
    space, user = owner_space(slug)
    data = body()
    if "file_id" not in data:
        raise service.MakerError("invalid_cover")
    social.set_cover(space, user, data["file_id"])
    db.session.commit()
    return jsonify({"cover_url": f"/api/makerspace/{space.slug}/cover?v={space.cover_file_id}" if space.cover_file_id else None})


@bp.get("/<slug>/cover")
@jwt_required(optional=True)
def get_cover(slug):
    from app.models.file import File
    from app.routes.file import _stream_file_from_oss
    space = service.get_space(slug, get_authenticated_user())
    record = File.query.filter_by(id=space.cover_file_id, file_type=File.MAKER_COVER, status="uploaded", is_deleted=False).first() if space.cover_file_id else None
    if not record or record.mime_type not in File.MAKER_COVER_MIMES:
        raise service.MakerError("not_found", 404)
    try:
        response = _stream_file_from_oss(record, cache_control="no-store")
        if hasattr(response, 'headers'):
            response.headers['Content-Security-Policy'] = "default-src 'none'; sandbox"
        return response
    except Exception:
        current_app.logger.warning("MakerSpace cover delivery failed for %s", space.id)
        raise service.MakerError("cover_unavailable", 502)


@bp.put("/<slug>")
@jwt_required()
def update(slug):
    space, user = owner_space(slug)
    editable(space)
    data = body()
    if data.get("version") != space.version:
        raise service.MakerError("conflicting_update", 409)
    service.update_fields(space, data)
    space.version += 1
    service.audit(space, user, "draft_updated", version=space.version)
    db.session.commit()
    return jsonify(service.serialize(space, user, private=True))


@bp.post("/<slug>/credentials")
@jwt_required()
def rotate_credentials(slug):
    space, user = owner_space(slug)
    editable(space)
    result = service.credentials(space, user)
    space.version += 1
    db.session.commit()
    result["webhook_path"] = f"/api/makerspace/hooks/{space.id}"
    return jsonify(result)


@bp.put("/<slug>/environment")
@jwt_required()
def environment(slug):
    space, user = owner_space(slug)
    editable(space)
    if space.kind != "hosted":
        raise service.MakerError("external_managed_separately", 409)
    data = body()
    values = data.get("values")
    if not isinstance(values, dict) or len(values) > 20:
        raise service.MakerError("invalid_environment")
    old = json.loads(service.decrypt(space.encrypted_environment) or "{}")
    for key, value in values.items():
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", key) or key in {"PATH", "HOME", "PORT", "HOST", "BASE_PATH", "LD_PRELOAD", "LD_LIBRARY_PATH", "NODE_OPTIONS", "PYTHONPATH"} or key.startswith(("UNIKORN_", "MAKERSPACE_")):
            raise service.MakerError("reserved_environment_name")
        if value is None:
            old.pop(key, None)
        elif isinstance(value, str) and len(value) <= 4096 and "\x00" not in value and "\n" not in value:
            old[key] = value
        else:
            raise service.MakerError("invalid_environment")
    if len(old) > 20:
        raise service.MakerError("invalid_environment")
    space.encrypted_environment = service.encrypt(json.dumps(old))
    space.version += 1
    service.audit(space, user, "environment_updated", names=sorted(values))
    db.session.commit()
    return jsonify({"names": sorted(old), "version": space.version})


@bp.post("/<slug>/deployments")
@jwt_required()
def deploy(slug):
    space, user = owner_space(slug)
    item = service.queue_deployment(space, user.id)
    service.audit(space, user, "deployment_queued", deployment_id=item.id)
    db.session.commit()
    return jsonify(service.deployment_payload(item, private=True)), 202


@bp.post("/<slug>/deployments/<identifier>/submit")
@jwt_required()
def submit(slug, identifier):
    space, user = owner_space(slug)
    item = service.deployment_for(space, identifier)
    service.submit(space, item, user)
    db.session.commit()
    return jsonify(service.deployment_payload(item, private=True))


@bp.post("/<slug>/deployments/<identifier>/withdraw")
@jwt_required()
def withdraw(slug, identifier):
    space, user = owner_space(slug)
    item = service.deployment_for(space, identifier)
    if item.review_status != "pending":
        raise service.MakerError("invalid_review_state", 409)
    item.review_status = "withdrawn"
    service.audit(space, user, "review_withdrawn", deployment_id=item.id)
    db.session.commit()
    return jsonify(service.deployment_payload(item, private=True))


@bp.post("/<slug>/archive")
@jwt_required()
def archive(slug):
    space, user = owner_space(slug)
    if space.kind == "external":
        raise service.MakerError("external_managed_separately", 409)
    space.status, space.auto_deploy = "archived", False
    MakerSession.query.filter_by(space_id=space.id).delete()
    service.audit(space, user, "archived")
    db.session.commit()
    return jsonify({"status": "archived"})


@bp.post("/<slug>/launch")
@jwt_required(optional=True)
def launch(slug):
    user = get_authenticated_user()
    space = service.get_space(slug, user)
    data = body()
    result, cookie = service.launch(space, user, data.get("deployment_id"))
    db.session.commit()
    response = jsonify(result)
    if cookie:
        identifier, secret = cookie
        response.set_cookie("makerspace_session", secret, max_age=3600, httponly=True, secure=True, samesite="None", partitioned=True, path=f"/api/makerspace/run/{identifier}/")
    return response


@bp.get("/admin/reviews")
@jwt_required()
def review_queue():
    user = service.active_user(get_authenticated_user())
    if not service.administrator(user):
        raise service.MakerError("admin_required", 403)
    items = MakerDeployment.query.filter_by(review_status="pending").order_by(MakerDeployment.submitted_at.asc()).limit(100).all()
    return jsonify({"reviews": [{"space": service.serialize(item.space, user, private=True), "deployment": service.deployment_payload(item, private=True)} for item in items]})


@bp.post("/admin/<slug>/deployments/<identifier>/review")
@jwt_required()
def review(slug, identifier):
    user = service.active_user(get_authenticated_user())
    if not service.administrator(user):
        raise service.MakerError("admin_required", 403)
    space = MakerSpace.query.filter_by(slug=slug).with_for_update().first()
    if not space:
        raise service.MakerError("not_found", 404)
    item, data = service.deployment_for(space, identifier), body()
    if data.get("source_sha") != item.source_sha or data.get("artifact_digest") != item.artifact_digest:
        raise service.MakerError("review_version_changed", 409)
    service.review(space, item, user, data.get("decision"), data.get("note", ""))
    db.session.commit()
    return jsonify(service.serialize(space, user, private=True))


@bp.get("/admin/<slug>/deployments/<identifier>/source")
@jwt_required()
def review_source(slug, identifier):
    user = service.active_user(get_authenticated_user())
    if not service.administrator(user):
        raise service.MakerError("admin_required", 403)
    space = service.get_space(slug, user)
    item = service.deployment_for(space, identifier)
    if item.review_status != "pending" or not re.fullmatch(r"[a-f0-9]{32}", item.id):
        raise service.MakerError("not_found", 404)
    path = Path(current_app.config['MAKERSPACE_REVIEW_DIRECTORY']) / (item.id + ".tar.gz")
    if not path.is_file() or path.is_symlink():
        raise service.MakerError("source_unavailable", 503)
    service.audit(space, user, "source_downloaded", deployment_id=item.id, source_sha=item.source_sha)
    db.session.commit()
    return send_file(path, as_attachment=True, download_name=f"{space.slug}-{item.source_sha}.tar.gz", mimetype="application/gzip", conditional=False)


@bp.post("/<slug>/deployments/<identifier>/retry-publication")
@jwt_required()
def retry_publication(slug, identifier):
    space, user = owner_space(slug)
    item = service.deployment_for(space, identifier)
    if item.review_status != "approved" or item.publication_status != "failed" or item.status != "ready" or space.status == "suspended":
        raise service.MakerError("invalid_review_state", 409)
    item.publication_status = "queued"
    service.audit(space, user, "publication_retried", deployment_id=item.id)
    db.session.commit()
    return jsonify(service.deployment_payload(item, private=True))


@bp.post("/admin/<slug>/suspend")
@jwt_required()
def suspend(slug):
    user = service.active_user(get_authenticated_user())
    if not service.administrator(user):
        raise service.MakerError("admin_required", 403)
    space = service.get_space(slug, user)
    note = service.text(body(), "note", 3000)
    if space.kind == "external":
        raise service.MakerError("external_managed_separately", 409)
    space.status, space.auto_deploy = "suspended", False
    MakerSession.query.filter_by(space_id=space.id).delete()
    service.audit(space, user, "suspended", note=note)
    db.session.commit()
    return jsonify({"status": "suspended"})


@bp.post("/hooks/<identifier>")
def webhook(identifier):
    if request.content_length and request.content_length > 1024 * 1024:
        raise service.MakerError("request_too_large", 413)
    space = MakerSpace.query.filter_by(id=identifier).with_for_update().first()
    if not space or not space.encrypted_webhook_secret or space.status in ("archived", "suspended"):
        raise service.MakerError("not_found", 404)
    raw = request.get_data()
    expected = "sha256=" + hmac.new(service.decrypt(space.encrypted_webhook_secret).encode(), raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, request.headers.get("X-Hub-Signature-256", "")):
        raise service.MakerError("invalid_signature", 403)
    if request.headers.get("X-GitHub-Event") == "ping":
        return jsonify({"status": "connected"})
    data = request.get_json(silent=True) or {}
    delivery = request.headers.get("X-GitHub-Delivery", "")
    if not re.fullmatch(r"[A-Za-z0-9-]{1,64}", delivery):
        raise service.MakerError("invalid_delivery")
    delivery_key = space.id + ":" + delivery
    if db.session.get(MakerWebhookDelivery, delivery_key):
        return jsonify({"status": "duplicate"})
    if request.headers.get("X-GitHub-Event") != "push" or data.get("deleted") or data.get("repository", {}).get("full_name", "").lower() != space.repository.lower() or data.get("ref") != f"refs/heads/{space.branch}" or not space.auto_deploy:
        return jsonify({"status": "ignored"})
    sha = data.get("after", "")
    if not isinstance(sha, str) or not service.SHA.fullmatch(sha):
        raise service.MakerError("invalid_sha")
    # Coalesce pushes while a build runs. GitHub does not retry failed hooks.
    # The durable desired SHA is admitted by the worker after the current build.
    space.pending_source_sha = sha
    db.session.add(MakerWebhookDelivery(id=delivery_key, space_id=space.id))
    db.session.commit()
    return jsonify({"status": "accepted", "source_sha": sha}), 202


def worker_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        token = current_app.config.get("MAKERSPACE_WORKER_TOKEN", "")
        if len(token) < 32 or not hmac.compare_digest(request.headers.get("Authorization", ""), "Bearer " + token):
            raise service.MakerError("not_found", 404)
        return fn(*args, **kwargs)
    return wrapped


@bp.post("/worker/lease")
@worker_required
def lease():
    data = body()
    identifier = data.get("worker_id", "")
    if identifier != current_app.config.get("MAKERSPACE_WORKER_ID", "school-makerspace"):
        raise service.MakerError("unknown_worker", 403)
    capabilities = data.get("capabilities", {})
    if capabilities.get("runtime") != "runsc" or capabilities.get("disk_quota") is not True or capabilities.get("network_isolation") is not True:
        raise service.MakerError("sandbox_not_ready", 503)
    worker = db.session.get(MakerWorker, identifier)
    if not worker:
        worker = MakerWorker(id=identifier)
        db.session.add(worker)
    worker.last_seen_at, worker.capabilities = now(), capabilities
    # A lost build is failed, never silently re-executed under an old lease.
    expired = MakerDeployment.query.filter((MakerDeployment.status == "building") | (MakerDeployment.publication_status == "publishing"), MakerDeployment.lease_expires_at < now()).all()
    for item in expired:
        if item.publication_status == "publishing":
            item.publication_status = "failed"
        else:
            item.status = "failed"
        item.error_code, item.finished_at = "worker_timeout", now()
    db.session.flush()
    job = None
    if current_app.config.get("MAKERSPACE_HOSTING_ENABLED", False):
        for space in MakerSpace.query.filter(MakerSpace.pending_source_sha.isnot(None), MakerSpace.auto_deploy.is_(True), MakerSpace.status.notin_(["suspended", "archived"])).with_for_update(skip_locked=True).all():
            try:
                service.queue_deployment(space, space.owner_id, space.pending_source_sha)
                space.pending_source_sha = None
            except service.MakerError as error:
                if error.code not in ("deployment_in_progress", "deployment_rate_limit", "connect_repository_first", "hosting_unavailable"):
                    raise
        item = MakerDeployment.query.join(MakerSpace).filter(MakerDeployment.publication_status == "queued", MakerDeployment.review_status == "approved", MakerSpace.status.notin_(["suspended", "archived"])).order_by(MakerDeployment.reviewed_at.asc()).with_for_update(skip_locked=True).first()
        kind = "publish" if item else "build"
        if not item:
            item = MakerDeployment.query.join(MakerSpace).filter(MakerDeployment.status == "queued", MakerSpace.status.notin_(["suspended", "archived"])).order_by(MakerDeployment.created_at.asc()).with_for_update(skip_locked=True).first()
        if item:
            secret = secrets.token_urlsafe(32)
            if kind == "publish":
                item.publication_status = "publishing"
            else:
                item.status = "building"
            item.worker_id, item.lease_hash, item.lease_expires_at = identifier, service.hash_value(secret), now() + timedelta(minutes=10)
            space = item.space
            job = {"kind": kind, "id": item.id, "space_id": space.id, "source_ref": item.source_ref, "source_sha": item.source_sha, "artifact_digest": item.artifact_digest, "snapshot": item.snapshot, "lease": secret, "private_key": service.decrypt(space.encrypted_private_key) if kind == "build" else None, "environment": json.loads(service.decrypt(item.encrypted_environment) or "{}"), "quota": service.QUOTA}
    # Runtime GC cannot delete the published version or a submitted review.
    keep = MakerDeployment.query.join(MakerSpace).filter(MakerDeployment.status == "ready", MakerSpace.status.notin_(["suspended", "archived"])).all()
    keep_ids = []
    for item in keep:
        latest = MakerDeployment.query.filter_by(space_id=item.space_id, status="ready").order_by(MakerDeployment.created_at.desc()).first()
        if item.id == item.space.published_deployment_id or item.review_status == "pending" or item.publication_status in ("queued", "publishing") or (latest and item.id == latest.id):
            keep_ids.append(item.id)
    # Revoke stale session targets in the database before the worker can reuse
    # their ports. Suspended and archived runtimes are revoked too.
    retired = MakerDeployment.query.filter(MakerDeployment.status == "ready", MakerDeployment.id.notin_(keep_ids)).all()
    for item in retired:
        item.status, item.runtime_port, item.public_runtime_port = "stopped", None, None
    MakerSession.query.filter(MakerSession.expires_at < now()).delete()
    MakerWebhookDelivery.query.filter(MakerWebhookDelivery.created_at < now() - timedelta(days=7)).delete()
    db.session.commit()
    return jsonify({"job": job, "keep_deployments": keep_ids})


@bp.post("/worker/heartbeat")
@worker_required
def heartbeat():
    identifier = body().get("worker_id")
    if identifier != current_app.config.get("MAKERSPACE_WORKER_ID", "school-makerspace"):
        raise service.MakerError("unknown_worker", 403)
    worker = db.session.get(MakerWorker, identifier)
    if not worker:
        raise service.MakerError("unknown_worker", 403)
    worker.last_seen_at = now()
    db.session.commit()
    return jsonify({"status": "ok"})


@bp.post("/worker/deployments/<identifier>")
@worker_required
def complete(identifier):
    item = MakerDeployment.query.filter_by(id=identifier).with_for_update().first()
    data = body()
    if not item or (item.status != "building" and item.publication_status != "publishing") or not item.lease_expires_at or service.aware(item.lease_expires_at) <= now() or not hmac.compare_digest(item.lease_hash or "", service.hash_value(str(data.get("lease", "")))):
        raise service.MakerError("invalid_lease", 409)
    publishing = item.publication_status == "publishing"
    status = data.get("status")
    if status not in ("ready", "failed"):
        raise service.MakerError("invalid_status")
    if status == "ready":
        if not service.SHA.fullmatch(data.get("source_sha", "")) or not re.fullmatch(r"[0-9a-f]{64}", data.get("artifact_digest", "")) or type(data.get("runtime_port")) is not int or not 20000 <= data["runtime_port"] < 20100:
            raise service.MakerError("invalid_runtime_receipt")
        if item.source_sha and item.source_sha != data["source_sha"]:
            raise service.MakerError("source_version_changed", 409)
        if publishing and item.artifact_digest != data["artifact_digest"]:
            raise service.MakerError("artifact_version_changed", 409)
        collision = MakerDeployment.query.filter(MakerDeployment.id != item.id, (MakerDeployment.runtime_port == data["runtime_port"]) | (MakerDeployment.public_runtime_port == data["runtime_port"])).first()
        if collision or (publishing and item.runtime_port == data["runtime_port"]):
            raise service.MakerError("runtime_port_in_use", 409)
        if item.space.status in ("suspended", "archived"):
            raise service.MakerError("space_suspended", 409)
        if publishing:
            item.public_runtime_port = data["runtime_port"]
            item.space.status = "published"
            item.space.published_deployment_id = item.id
            item.space.published_metadata = item.snapshot["metadata"]
            service.audit(item.space, None, "published", deployment_id=item.id, source_sha=item.source_sha, artifact_digest=item.artifact_digest)
        else:
            item.source_sha, item.artifact_digest, item.runtime_port = data["source_sha"], data["artifact_digest"], data["runtime_port"]
    if publishing:
        item.publication_status = "published" if status == "ready" else "failed"
    else:
        item.status = status
    item.finished_at = now()
    item.error_code = "build_failed" if status == "failed" else None
    item.log = service.redact_log(data.get("log", ""), [service.decrypt(item.space.encrypted_private_key), *json.loads(service.decrypt(item.encrypted_environment) or "{}").values()])
    item.resource_usage = {key: value for key, value in data.get("resource_usage", {}).items() if key in ("storage_bytes", "build_seconds") and type(value) in (int, float) and value >= 0}
    item.lease_hash = None
    db.session.commit()
    return jsonify({"status": item.status})


@bp.route("/run/<identifier>/", defaults={"path": ""}, methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
@bp.route("/run/<identifier>/<path:path>", methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
def runtime(identifier, path):
    from app.services.makerspace_proxy import proxy_runtime
    return proxy_runtime(identifier, path)


# Exchange authorization is separate from source/publication approval.
@bp.get('/<slug>/sync')
@jwt_required()
def sync_list(slug):
    from app.models.makerspace_sync import MakerSyncGrant
    from app.services import makerspace_sync as sync
    space, _user = owner_space(slug)
    grants = MakerSyncGrant.query.filter_by(space_id=space.id).order_by(MakerSyncGrant.created_at.desc()).limit(100).all()
    return jsonify({'grants': [sync.payload(grant) for grant in grants], 'runtime_ready': sync.runtime_ready()})


@bp.post('/<slug>/sync')
@jwt_required()
def sync_create(slug):
    from app.services import makerspace_sync as sync
    space, user = owner_space(slug)
    grant = sync.create(space, user, body())
    db.session.commit()
    return jsonify(sync.payload(grant)), 201


@bp.get('/admin/sync')
@jwt_required()
def sync_review_queue():
    from app.models.makerspace_sync import MakerSyncGrant
    from app.services import makerspace_sync as sync
    user = service.active_user(get_authenticated_user())
    if not service.administrator(user):
        raise service.MakerError('admin_required', 403)
    grants = MakerSyncGrant.query.order_by(MakerSyncGrant.created_at.desc()).limit(200).all()
    return jsonify({'grants': [sync.payload(grant) for grant in grants], 'runtime_ready': sync.runtime_ready()})


@bp.post('/admin/sync/<identifier>/review')
@jwt_required()
def sync_review(identifier):
    from app.services import makerspace_sync as sync
    user = service.active_user(get_authenticated_user())
    if not service.administrator(user):
        raise service.MakerError('admin_required', 403)
    grant = sync.locked_grant(identifier)
    sync.review(grant, user, body())
    db.session.commit()
    return jsonify(sync.payload(grant))


def managed_grant(identifier, *, owner_only=False):
    from app.services import makerspace_sync as sync
    user = service.active_user(get_authenticated_user())
    grant = sync.locked_grant(identifier)
    if not service.can_manage(grant.space, user) and (owner_only or not service.administrator(user)):
        raise service.MakerError('not_found', 404)
    return grant, user


@bp.post('/sync/<identifier>/revoke')
@jwt_required()
def sync_revoke(identifier):
    from app.services import makerspace_sync as sync
    grant, user = managed_grant(identifier)
    sync.revoke(grant, user)
    db.session.commit()
    return jsonify(sync.payload(grant))


@bp.post('/sync/<identifier>/credential')
@jwt_required()
def sync_credential(identifier):
    from app.services import makerspace_sync as sync
    grant, user = managed_grant(identifier, owner_only=True)
    token = sync.rotate(grant, user)
    db.session.commit()
    return jsonify({'token': token, 'gateway_path': f'/api/makerspace/exchange/{grant.id}'})


@bp.get('/sync/<identifier>/audit')
@jwt_required()
def sync_audit(identifier):
    from app.models.makerspace_sync import MakerSyncAudit
    grant, _user = managed_grant(identifier)
    events = MakerSyncAudit.query.filter_by(grant_id=grant.id).order_by(MakerSyncAudit.id.desc()).limit(100).all()
    return jsonify({'events': [{'id': event.id, 'action': event.action, 'record_count': event.record_count,
                               'created_at': service.aware(event.created_at).isoformat()} for event in events]})


@bp.post('/exchange/<identifier>')
def sync_exchange(identifier):
    from app.services import makerspace_sync as sync
    data = body()
    grant = sync.authenticate(identifier, request.headers.get('Authorization', ''))
    try:
        result = sync.exchange(grant, data)
    except service.MakerError as error:
        # Count failed authenticated calls too; do not persist student payloads.
        sync.log(grant, 'exchange_failed')
        db.session.commit()
        raise error
    db.session.commit()
    return jsonify(result)
