from datetime import datetime, timezone
from app.extensions import db

class TokenBlacklist(db.Model):
    """Model for storing revoked tokens"""
    __tablename__ = 'jwt_token_blacklist'
    
    id = db.Column(db.Integer, primary_key=True)
    jti = db.Column(db.String(36), unique=True, nullable=False)
    token_type = db.Column(db.String(10), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    revoked_at = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    expires = db.Column(db.DateTime(timezone=True), nullable=False)
    
    def to_dict(self):
        return {
            'token_id': self.id,
            'jti': self.jti,
            'token_type': self.token_type,
            'user_id': self.user_id,
            'revoked_at': self.revoked_at.isoformat(),
            'expires': self.expires.isoformat()
        }
    
    @classmethod
    def is_token_revoked(cls, jti):
        """Check if a token is in the blacklist"""
        return cls.query.filter_by(jti=jti).first() is not None
    
    
class STSTokenPool(db.Model):
    """Model for managing shared STS tokens pool"""
    __tablename__ = 'sts_token_pool'
    
    id = db.Column(db.Integer, primary_key=True)
    access_key_id = db.Column(db.String(64), nullable=False, unique=True)
    access_key_secret = db.Column(db.String(64), nullable=False)
    security_token = db.Column(db.Text, nullable=False)
    expiration = db.Column(db.DateTime(timezone=True), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=datetime.now(timezone.utc))
    
    @property
    def is_valid(self):
        return self.expiration > datetime.now(timezone.utc) + timedelta(minutes=5)