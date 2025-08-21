# app/models/oauth_authorization_code.py
from datetime import datetime, timezone, timedelta
from app.extensions import db
import secrets
import string

class OAuthAuthorizationCode(db.Model):
    __tablename__ = 'oauth_authorization_codes'
    
    id = db.Column(db.Integer, primary_key=True)
    
    # Authorization code data
    code = db.Column(db.String(255), unique=True, nullable=False, index=True)
    
    # Relationships
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    client_id = db.Column(db.String(40), db.ForeignKey('oauth_clients.client_id'), nullable=False)
    
    # OAuth2 Authorization Code Metadata
    redirect_uri = db.Column(db.String(255), nullable=False)
    scope = db.Column(db.Text)  # Space-separated scopes
    code_challenge = db.Column(db.String(128))  # PKCE code challenge
    code_challenge_method = db.Column(db.String(10))  # PKCE method (S256, plain)
    
    # Timestamps
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False)
    
    # Status
    used = db.Column(db.Boolean, default=False, nullable=False)
    used_at = db.Column(db.DateTime(timezone=True))
    
    # Relationships
    user = db.relationship('User', backref=db.backref('oauth_auth_codes', lazy='dynamic'))
    client = db.relationship('OAuthClient', backref=db.backref('auth_codes', lazy='dynamic'))
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if not self.code:
            self.code = self.generate_code()
        if not self.expires_at:
            # Authorization codes expire in 10 minutes
            self.expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)
    
    @staticmethod
    def generate_code(length=40):
        """Generate a secure random authorization code"""
        alphabet = string.ascii_letters + string.digits
        return ''.join(secrets.choice(alphabet) for _ in range(length))
    
    def is_expired(self):
        """Check if the authorization code is expired"""
        return datetime.now(timezone.utc) > self.expires_at
    
    def is_valid(self):
        """Check if the code is valid (not expired and not used)"""
        return not self.used and not self.is_expired()
    
    def use(self):
        """Mark the code as used"""
        self.used = True
        self.used_at = datetime.now(timezone.utc)
    
    def verify_code_challenge(self, code_verifier):
        """Verify PKCE code challenge"""
        if not self.code_challenge or not self.code_challenge_method:
            return True  # PKCE not required for this request
        
        if self.code_challenge_method == 'S256':
            import hashlib
            import base64
            
            # Create SHA256 hash of code_verifier
            digest = hashlib.sha256(code_verifier.encode('utf-8')).digest()
            # Base64 URL-safe encode without padding
            challenge = base64.urlsafe_b64encode(digest).decode('utf-8').rstrip('=')
            return challenge == self.code_challenge
        
        elif self.code_challenge_method == 'plain':
            return code_verifier == self.code_challenge
        
        return False
    
    def get_scope(self):
        """Get scopes as a set"""
        if self.scope:
            return set(self.scope.split(' '))
        return set()
    
    def to_dict(self):
        return {
            'code': self.code,
            'client_id': self.client_id,
            'user_id': self.user_id,
            'redirect_uri': self.redirect_uri,
            'scope': self.scope,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'expires_at': self.expires_at.isoformat() if self.expires_at else None,
            'used': self.used
        }