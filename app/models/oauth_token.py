# app/models/oauth_token.py
from datetime import datetime, timezone, timedelta
from app.extensions import db

class OAuthToken(db.Model):
    __tablename__ = 'oauth_tokens'
    
    id = db.Column(db.Integer, primary_key=True)
    
    # Token data
    access_token = db.Column(db.String(255), unique=True, nullable=False, index=True)
    refresh_token = db.Column(db.String(255), unique=True, nullable=True, index=True)
    token_type = db.Column(db.String(40), default='Bearer', nullable=False)
    
    # Relationships
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    client_id = db.Column(db.String(40), db.ForeignKey('oauth_clients.client_id'), nullable=False)
    
    # OAuth2 Token Metadata  
    scope = db.Column(db.Text)  # Space-separated scopes
    expires_in = db.Column(db.Integer, default=3600)  # Token lifetime in seconds
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False)
    
    # Timestamps
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
                          onupdate=lambda: datetime.now(timezone.utc), nullable=False)
    
    # Status
    revoked = db.Column(db.Boolean, default=False, nullable=False)
    revoked_at = db.Column(db.DateTime(timezone=True))
    
    # Relationships
    user = db.relationship('User', backref=db.backref('oauth_tokens', lazy='dynamic'))
    client = db.relationship('OAuthClient', backref=db.backref('tokens', lazy='dynamic'))
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if not self.expires_at and self.expires_in:
            self.expires_at = datetime.now(timezone.utc) + timedelta(seconds=self.expires_in)
    
    def is_expired(self):
        """Check if the token is expired"""
        return datetime.now(timezone.utc) > self.expires_at
    
    def is_valid(self):
        """Check if the token is valid (not expired and not revoked)"""
        return not self.revoked and not self.is_expired()
    
    def revoke(self):
        """Revoke the token"""
        self.revoked = True
        self.revoked_at = datetime.now(timezone.utc)
    
    def get_scope(self):
        """Get scopes as a set"""
        if self.scope:
            return set(self.scope.split(' '))
        return set()
    
    def check_scope(self, scope):
        """Check if token has the required scope"""
        token_scopes = self.get_scope()
        required_scopes = set(scope.split(' ')) if isinstance(scope, str) else set(scope)
        return required_scopes.issubset(token_scopes)
    
    def to_dict(self):
        return {
            'access_token': self.access_token,
            'token_type': self.token_type,
            'expires_in': self.expires_in,
            'scope': self.scope,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'expires_at': self.expires_at.isoformat() if self.expires_at else None
        }