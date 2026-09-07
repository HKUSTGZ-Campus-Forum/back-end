"""UniKorn-owned catalog and control plane; creator runtime data stays isolated."""
from datetime import datetime, timezone
import uuid

from app.extensions import db


def now():
    return datetime.now(timezone.utc)


def identifier():
    return uuid.uuid4().hex


class MakerSpace(db.Model):
    __tablename__ = "maker_spaces"
    id = db.Column(db.String(32), primary_key=True, default=identifier)
    slug = db.Column(db.String(40), nullable=False, unique=True)
    owner_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    title_zh = db.Column(db.String(100), nullable=False)
    title_en = db.Column(db.String(100), nullable=False)
    description_zh = db.Column(db.Text, nullable=False, default="")
    description_en = db.Column(db.Text, nullable=False, default="")
    category = db.Column(db.String(24), nullable=False, default="tools")
    status = db.Column(db.String(20), nullable=False, default="draft", index=True)
    # external is an administrator-provisioned, previously audited integration.
    kind = db.Column(db.String(20), nullable=False, default="hosted")
    external_path = db.Column(db.String(160))
    repository = db.Column(db.String(200), nullable=False, default="")
    branch = db.Column(db.String(100), nullable=False, default="main")
    settings = db.Column(db.JSON, nullable=False, default=dict)
    public_key = db.Column(db.Text)
    encrypted_private_key = db.Column(db.Text)
    encrypted_webhook_secret = db.Column(db.Text)
    encrypted_environment = db.Column(db.Text)
    auto_deploy = db.Column(db.Boolean, nullable=False, default=False)
    pending_source_sha = db.Column(db.String(40))
    published_deployment_id = db.Column(db.String(32))
    published_metadata = db.Column(db.JSON)
    version = db.Column(db.Integer, nullable=False, default=1)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=now)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=now, onupdate=now)
    cover_file_id = db.Column(db.Integer, db.ForeignKey("files.id"), nullable=True, index=True)
    owner = db.relationship("User", foreign_keys=[owner_id])


class MakerDeployment(db.Model):
    __tablename__ = "maker_deployments"
    id = db.Column(db.String(32), primary_key=True, default=identifier)
    space_id = db.Column(db.String(32), db.ForeignKey("maker_spaces.id"), nullable=False, index=True)
    requested_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    source_sha = db.Column(db.String(40))
    source_ref = db.Column(db.String(100), nullable=False)
    snapshot = db.Column(db.JSON, nullable=False)
    status = db.Column(db.String(20), nullable=False, default="queued", index=True)
    review_status = db.Column(db.String(20), nullable=False, default="draft", index=True)
    review_note = db.Column(db.Text)
    reviewed_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    reviewed_at = db.Column(db.DateTime(timezone=True))
    submitted_at = db.Column(db.DateTime(timezone=True))
    log = db.Column(db.Text, nullable=False, default="")
    error_code = db.Column(db.String(80))
    artifact_digest = db.Column(db.String(64))
    worker_id = db.Column(db.String(64))
    lease_hash = db.Column(db.String(64))
    lease_expires_at = db.Column(db.DateTime(timezone=True))
    runtime_port = db.Column(db.Integer)
    publication_status = db.Column(db.String(20), nullable=False, default="none", index=True)
    public_runtime_port = db.Column(db.Integer)
    encrypted_environment = db.Column(db.Text)
    resource_usage = db.Column(db.JSON, nullable=False, default=dict)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=now)
    finished_at = db.Column(db.DateTime(timezone=True))
    space = db.relationship("MakerSpace", foreign_keys=[space_id])


class MakerAudit(db.Model):
    __tablename__ = "maker_audit_events"
    id = db.Column(db.Integer, primary_key=True)
    space_id = db.Column(db.String(32), db.ForeignKey("maker_spaces.id"), nullable=False, index=True)
    actor_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    action = db.Column(db.String(40), nullable=False)
    details = db.Column(db.JSON, nullable=False, default=dict)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=now)


class MakerSession(db.Model):
    __tablename__ = "maker_runtime_sessions"
    id = db.Column(db.String(64), primary_key=True)
    cookie_hash = db.Column(db.String(64), nullable=False)
    runtime_cookie_hash = db.Column(db.String(64))
    bootstrap_hash = db.Column(db.String(64))
    bootstrap_expires_at = db.Column(db.DateTime(timezone=True))
    space_id = db.Column(db.String(32), db.ForeignKey("maker_spaces.id"), nullable=False, index=True)
    deployment_id = db.Column(db.String(32), db.ForeignKey("maker_deployments.id"), nullable=False)
    viewer_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    private = db.Column(db.Boolean, nullable=False)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False, index=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=now)


class MakerWebhookDelivery(db.Model):
    __tablename__ = "maker_webhook_deliveries"
    id = db.Column(db.String(100), primary_key=True)
    space_id = db.Column(db.String(32), db.ForeignKey("maker_spaces.id"), nullable=False, index=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=now)


class MakerWorker(db.Model):
    __tablename__ = "maker_workers"
    id = db.Column(db.String(64), primary_key=True)
    last_seen_at = db.Column(db.DateTime(timezone=True), nullable=False, default=now)
    capabilities = db.Column(db.JSON, nullable=False, default=dict)


class MakerLike(db.Model):
    __tablename__ = "maker_likes"
    space_id = db.Column(db.String(32), db.ForeignKey("maker_spaces.id"), primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), primary_key=True, index=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=now)


class MakerFavorite(db.Model):
    __tablename__ = "maker_favorites"
    space_id = db.Column(db.String(32), db.ForeignKey("maker_spaces.id"), primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), primary_key=True, index=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=now)
