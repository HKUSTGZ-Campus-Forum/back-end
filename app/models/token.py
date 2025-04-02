from datetime import datetime, timezone
from app.extensions import db

class TokenBlacklist(db.Model):
    """Model for storing revoked tokens"""
    __tablename__ = 'token_blacklist'
    
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