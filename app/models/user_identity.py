from datetime import datetime, timezone
from app.extensions import db
from sqlalchemy.dialects.postgresql import JSONB

class UserIdentity(db.Model):
    __tablename__ = 'user_identities'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    identity_type_id = db.Column(db.Integer, db.ForeignKey('identity_types.id'), nullable=False)
    status = db.Column(db.String(20), default='pending', nullable=False)  # 'pending', 'approved', 'rejected', 'revoked'
    verification_documents = db.Column(JSONB)  # Store file IDs and metadata
    verified_by = db.Column(db.Integer, db.ForeignKey('users.id'))  # Admin who approved/rejected
    rejection_reason = db.Column(db.Text)
    notes = db.Column(db.Text)  # Admin notes
    verified_at = db.Column(db.DateTime(timezone=True))
    expires_at = db.Column(db.DateTime(timezone=True))  # Optional expiration
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), 
                          onupdate=lambda: datetime.now(timezone.utc), nullable=False)
    
    # Relationships
    user = db.relationship('User', foreign_keys=[user_id], backref='identities')
    verifier = db.relationship('User', foreign_keys=[verified_by])
    
    # Status constants
    PENDING = 'pending'
    APPROVED = 'approved'
    REJECTED = 'rejected'
    REVOKED = 'revoked'
    
    # Constraints
    __table_args__ = (
        db.UniqueConstraint('user_id', 'identity_type_id', name='uq_user_identity_type'),
        db.Index('idx_user_identities_status', 'status'),
        db.Index('idx_user_identities_user_status', 'user_id', 'status'),
        db.CheckConstraint("status IN ('pending', 'approved', 'rejected', 'revoked')", name='ck_user_identities_status')
    )
    
    def to_dict(self, include_documents=False, include_admin_info=False):
        data = {
            "id": self.id,
            "user_id": self.user_id,
            "identity_type_id": self.identity_type_id,
            "status": self.status,
            "verified_at": self.verified_at.isoformat() if self.verified_at else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None
        }
        
        # Include identity type information
        if self.identity_type:
            data["identity_type"] = self.identity_type.to_dict()
        
        # Include documents for admins or the user themselves
        if include_documents and self.verification_documents:
            data["verification_documents"] = self.verification_documents
        
        # Include admin-specific information
        if include_admin_info:
            data["verified_by"] = self.verified_by
            data["rejection_reason"] = self.rejection_reason
            data["notes"] = self.notes
            if self.verifier:
                data["verifier"] = {
                    "id": self.verifier.id,
                    "username": self.verifier.username
                }
        
        return data
    
    def is_active(self):
        """Check if this identity verification is currently active"""
        if self.status != self.APPROVED:
            return False
        
        # Check expiration
        if self.expires_at and datetime.now(timezone.utc) > self.expires_at:
            return False
        
        return True
    
    def approve(self, admin_user_id, notes=None):
        """Approve this identity verification"""
        self.status = self.APPROVED
        self.verified_by = admin_user_id
        self.verified_at = datetime.now(timezone.utc)
        self.notes = notes
        self.rejection_reason = None  # Clear any previous rejection reason
    
    def reject(self, admin_user_id, reason, notes=None):
        """Reject this identity verification"""
        self.status = self.REJECTED
        self.verified_by = admin_user_id
        self.verified_at = datetime.now(timezone.utc)
        self.rejection_reason = reason
        self.notes = notes
    
    def revoke(self, admin_user_id, reason, notes=None):
        """Revoke this identity verification"""
        self.status = self.REVOKED
        self.verified_by = admin_user_id
        self.verified_at = datetime.now(timezone.utc)
        self.rejection_reason = reason
        self.notes = notes
    
    @classmethod
    def get_user_active_identities(cls, user_id):
        """Get all active identity verifications for a user"""
        return cls.query.filter_by(
            user_id=user_id,
            status=cls.APPROVED
        ).filter(
            db.or_(
                cls.expires_at.is_(None),
                cls.expires_at > datetime.now(timezone.utc)
            )
        ).all()
    
    @classmethod
    def get_pending_verifications(cls):
        """Get all pending verification requests for admin review"""
        return cls.query.filter_by(status=cls.PENDING).order_by(cls.created_at.asc()).all()
    
    def __repr__(self):
        return f'<UserIdentity {self.user_id}:{self.identity_type_id} ({self.status})>'