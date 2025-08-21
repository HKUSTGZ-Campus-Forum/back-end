# app/models/oauth_client.py
from datetime import datetime, timezone
from app.extensions import db

class OAuthClient(db.Model):
    __tablename__ = 'oauth_clients'
    
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.String(40), unique=True, nullable=False, index=True)
    client_secret = db.Column(db.String(55), nullable=False)
    client_name = db.Column(db.String(100), nullable=False)
    client_description = db.Column(db.Text)
    
    # OAuth2 Client Metadata
    client_uri = db.Column(db.String(255))
    redirect_uris = db.Column(db.Text)  # JSON array of allowed redirect URIs
    scope = db.Column(db.Text)  # Space-separated scopes
    response_types = db.Column(db.Text)  # Space-separated response types
    grant_types = db.Column(db.Text)  # Space-separated grant types
    
    # Timestamps
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
                          onupdate=lambda: datetime.now(timezone.utc), nullable=False)
    
    # Status
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    
    def get_redirect_uris(self):
        """Get redirect URIs as a list"""
        if self.redirect_uris:
            import json
            try:
                return json.loads(self.redirect_uris)
            except:
                return []
        return []
    
    def set_redirect_uris(self, uris):
        """Set redirect URIs from a list"""
        import json
        self.redirect_uris = json.dumps(uris)
    
    def get_allowed_scope(self, scope):
        """Check if the requested scope is allowed"""
        if not self.scope:
            return ''
        
        allowed = set(self.scope.split(' '))
        requested = set(scope.split(' '))
        return ' '.join(allowed & requested)
    
    def check_redirect_uri(self, redirect_uri):
        """Check if redirect URI is allowed"""
        allowed_uris = self.get_redirect_uris()
        return redirect_uri in allowed_uris
    
    def check_response_type(self, response_type):
        """Check if response type is allowed"""
        if not self.response_types:
            return False
        return response_type in self.response_types.split(' ')
    
    def check_grant_type(self, grant_type):
        """Check if grant type is allowed"""
        if not self.grant_types:
            return False
        return grant_type in self.grant_types.split(' ')
    
    def to_dict(self):
        return {
            'client_id': self.client_id,
            'client_name': self.client_name,
            'client_description': self.client_description,
            'client_uri': self.client_uri,
            'redirect_uris': self.get_redirect_uris(),
            'scope': self.scope,
            'is_active': self.is_active,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }