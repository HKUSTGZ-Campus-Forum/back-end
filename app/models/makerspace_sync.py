"""Approval metadata only. Business records stay in each application's database."""
from app.extensions import db
from app.models.makerspace import identifier, now


class MakerIdentity(db.Model):
    __tablename__ = 'maker_identities'
    space_id = db.Column(db.String(32), db.ForeignKey('maker_spaces.id'), primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), primary_key=True)
    subject = db.Column(db.String(32), nullable=False, unique=True, default=identifier)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=now)


class MakerSyncGrant(db.Model):
    __tablename__ = 'maker_sync_grants'
    id = db.Column(db.String(32), primary_key=True, default=identifier)
    space_id = db.Column(db.String(32), db.ForeignKey('maker_spaces.id'), nullable=False, index=True)
    deployment_id = db.Column(db.String(32), db.ForeignKey('maker_deployments.id'), nullable=False)
    requested_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    policy = db.Column(db.JSON, nullable=False)
    policy_digest = db.Column(db.String(64), nullable=False)
    status = db.Column(db.String(20), nullable=False, default='pending', index=True)
    reviewed_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    reviewed_at = db.Column(db.DateTime(timezone=True))
    review_note = db.Column(db.Text)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False)
    token_hash = db.Column(db.String(64))
    token_rotated_at = db.Column(db.DateTime(timezone=True))
    window_start = db.Column(db.DateTime(timezone=True))
    window_count = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=now)
    space = db.relationship('MakerSpace')
    deployment = db.relationship('MakerDeployment')


class MakerSyncReceipt(db.Model):
    __tablename__ = 'maker_sync_receipts'
    grant_id = db.Column(db.String(32), db.ForeignKey('maker_sync_grants.id'), primary_key=True)
    event_id = db.Column(db.String(64), primary_key=True)
    payload_hash = db.Column(db.String(64), nullable=False)
    result = db.Column(db.JSON, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=now)


class MakerSyncAudit(db.Model):
    __tablename__ = 'maker_sync_audits'
    id = db.Column(db.Integer, primary_key=True)
    grant_id = db.Column(db.String(32), db.ForeignKey('maker_sync_grants.id'), nullable=False, index=True)
    actor_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    action = db.Column(db.String(32), nullable=False)
    # No payloads, credentials, free-form remote errors or student details.
    record_count = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=now)
