# test_oauth_models.py
import unittest
from datetime import datetime, timezone, timedelta
from app import create_app
from app.extensions import db
from app.models.user import User
from app.models.user_role import UserRole
from app.models.oauth_client import OAuthClient
from app.models.oauth_token import OAuthToken
from app.models.oauth_authorization_code import OAuthAuthorizationCode

class TestOAuthModels(unittest.TestCase):
    def setUp(self):
        """Set up test environment"""
        self.app = create_app()
        self.app.config['TESTING'] = True
        self.app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
        
        self.app_context = self.app.app_context()
        self.app_context.push()
        
        db.create_all()
        
        # Create test user role
        user_role = UserRole(name='student')
        db.session.add(user_role)
        db.session.commit()
        
        # Create test user
        self.test_user = User(
            username='testuser',
            email='test@example.com',
            role_id=user_role.id
        )
        self.test_user.set_password('testpass')
        db.session.add(self.test_user)
        db.session.commit()
        
        # Create test OAuth client
        self.test_client = OAuthClient(
            client_id='test_client_id',
            client_secret='test_client_secret',
            client_name='Test Client',
            client_description='Test OAuth client',
            client_uri='https://example.com',
            redirect_uris='["https://example.com/callback", "http://localhost:3000/callback"]',
            scope='profile email',
            response_types='code',
            grant_types='authorization_code'
        )
        db.session.add(self.test_client)
        db.session.commit()
    
    def tearDown(self):
        """Clean up test environment"""
        db.session.remove()
        db.drop_all()
        self.app_context.pop()
    
    def test_oauth_client_creation(self):
        """Test OAuth client model creation"""
        client = self.test_client
        
        self.assertEqual(client.client_id, 'test_client_id')
        self.assertEqual(client.client_name, 'Test Client')
        self.assertTrue(client.is_active)
        self.assertIsNotNone(client.created_at)
    
    def test_oauth_client_redirect_uris(self):
        """Test OAuth client redirect URI handling"""
        client = self.test_client
        
        # Test getting redirect URIs
        uris = client.get_redirect_uris()
        expected_uris = ["https://example.com/callback", "http://localhost:3000/callback"]
        self.assertEqual(uris, expected_uris)
        
        # Test setting redirect URIs
        new_uris = ["https://newsite.com/callback"]
        client.set_redirect_uris(new_uris)
        self.assertEqual(client.get_redirect_uris(), new_uris)
        
        # Test checking redirect URI
        self.assertTrue(client.check_redirect_uri("https://newsite.com/callback"))
        self.assertFalse(client.check_redirect_uri("https://malicious.com/callback"))
    
    def test_oauth_client_scopes(self):
        """Test OAuth client scope handling"""
        client = self.test_client
        
        # Test allowed scope filtering
        requested_scope = "profile email courses"
        allowed_scope = client.get_allowed_scope(requested_scope)
        self.assertEqual(allowed_scope, "profile email")  # courses not allowed
        
        # Test with invalid scopes
        requested_scope = "invalid_scope profile"
        allowed_scope = client.get_allowed_scope(requested_scope)
        self.assertEqual(allowed_scope, "profile")
    
    def test_oauth_client_validations(self):
        """Test OAuth client validation methods"""
        client = self.test_client
        
        # Test response type validation
        self.assertTrue(client.check_response_type('code'))
        self.assertFalse(client.check_response_type('token'))
        
        # Test grant type validation
        self.assertTrue(client.check_grant_type('authorization_code'))
        self.assertFalse(client.check_grant_type('implicit'))
    
    def test_authorization_code_creation(self):
        """Test OAuth authorization code creation"""
        auth_code = OAuthAuthorizationCode(
            user_id=self.test_user.id,
            client_id=self.test_client.client_id,
            redirect_uri='https://example.com/callback',
            scope='profile email'
        )
        db.session.add(auth_code)
        db.session.commit()
        
        self.assertIsNotNone(auth_code.code)
        self.assertEqual(len(auth_code.code), 40)  # Default code length
        self.assertEqual(auth_code.user_id, self.test_user.id)
        self.assertEqual(auth_code.scope, 'profile email')
        self.assertFalse(auth_code.used)
        self.assertTrue(auth_code.is_valid())
    
    def test_authorization_code_expiration(self):
        """Test OAuth authorization code expiration"""
        # Create expired code
        auth_code = OAuthAuthorizationCode(
            user_id=self.test_user.id,
            client_id=self.test_client.client_id,
            redirect_uri='https://example.com/callback',
            scope='profile',
            expires_at=datetime.now(timezone.utc) - timedelta(minutes=1)  # Expired 1 minute ago
        )
        db.session.add(auth_code)
        db.session.commit()
        
        self.assertTrue(auth_code.is_expired())
        self.assertFalse(auth_code.is_valid())
    
    def test_authorization_code_usage(self):
        """Test OAuth authorization code usage"""
        auth_code = OAuthAuthorizationCode(
            user_id=self.test_user.id,
            client_id=self.test_client.client_id,
            redirect_uri='https://example.com/callback',
            scope='profile'
        )
        db.session.add(auth_code)
        db.session.commit()
        
        # Initially valid and unused
        self.assertTrue(auth_code.is_valid())
        self.assertFalse(auth_code.used)
        
        # Use the code
        auth_code.use()
        self.assertTrue(auth_code.used)
        self.assertIsNotNone(auth_code.used_at)
        self.assertFalse(auth_code.is_valid())
    
    def test_authorization_code_pkce(self):
        """Test OAuth authorization code PKCE verification"""
        auth_code = OAuthAuthorizationCode(
            user_id=self.test_user.id,
            client_id=self.test_client.client_id,
            redirect_uri='https://example.com/callback',
            scope='profile',
            code_challenge='E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM',  # SHA256 of 'dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk'
            code_challenge_method='S256'
        )
        db.session.add(auth_code)
        db.session.commit()
        
        # Valid verifier
        self.assertTrue(auth_code.verify_code_challenge('dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk'))
        
        # Invalid verifier
        self.assertFalse(auth_code.verify_code_challenge('invalid_verifier'))
    
    def test_oauth_token_creation(self):
        """Test OAuth token creation"""
        token = OAuthToken(
            access_token='test_access_token',
            refresh_token='test_refresh_token',
            user_id=self.test_user.id,
            client_id=self.test_client.client_id,
            scope='profile email',
            expires_in=3600
        )
        db.session.add(token)
        db.session.commit()
        
        self.assertEqual(token.access_token, 'test_access_token')
        self.assertEqual(token.token_type, 'Bearer')
        self.assertEqual(token.user_id, self.test_user.id)
        self.assertEqual(token.scope, 'profile email')
        self.assertFalse(token.revoked)
        self.assertTrue(token.is_valid())
    
    def test_oauth_token_expiration(self):
        """Test OAuth token expiration"""
        # Create expired token
        token = OAuthToken(
            access_token='expired_token',
            user_id=self.test_user.id,
            client_id=self.test_client.client_id,
            expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)  # Expired 1 second ago
        )
        db.session.add(token)
        db.session.commit()
        
        self.assertTrue(token.is_expired())
        self.assertFalse(token.is_valid())
    
    def test_oauth_token_revocation(self):
        """Test OAuth token revocation"""
        token = OAuthToken(
            access_token='revoke_test_token',
            user_id=self.test_user.id,
            client_id=self.test_client.client_id,
            scope='profile'
        )
        db.session.add(token)
        db.session.commit()
        
        # Initially valid and not revoked
        self.assertTrue(token.is_valid())
        self.assertFalse(token.revoked)
        
        # Revoke the token
        token.revoke()
        self.assertTrue(token.revoked)
        self.assertIsNotNone(token.revoked_at)
        self.assertFalse(token.is_valid())
    
    def test_oauth_token_scopes(self):
        """Test OAuth token scope handling"""
        token = OAuthToken(
            access_token='scope_test_token',
            user_id=self.test_user.id,
            client_id=self.test_client.client_id,
            scope='profile email courses'
        )
        db.session.add(token)
        db.session.commit()
        
        # Test scope checking
        self.assertTrue(token.check_scope('profile'))
        self.assertTrue(token.check_scope('profile email'))
        self.assertTrue(token.check_scope(['profile', 'email']))
        self.assertFalse(token.check_scope('admin'))
        self.assertFalse(token.check_scope('profile email admin'))
    
    def test_relationships(self):
        """Test model relationships"""
        # Create authorization code
        auth_code = OAuthAuthorizationCode(
            user_id=self.test_user.id,
            client_id=self.test_client.client_id,
            redirect_uri='https://example.com/callback',
            scope='profile'
        )
        db.session.add(auth_code)
        
        # Create token
        token = OAuthToken(
            access_token='relationship_test_token',
            user_id=self.test_user.id,
            client_id=self.test_client.client_id,
            scope='profile'
        )
        db.session.add(token)
        db.session.commit()
        
        # Test user relationships
        self.assertEqual(len(self.test_user.oauth_auth_codes.all()), 1)
        self.assertEqual(len(self.test_user.oauth_tokens.all()), 1)
        
        # Test client relationships
        self.assertEqual(len(self.test_client.auth_codes.all()), 1)
        self.assertEqual(len(self.test_client.tokens.all()), 1)
        
        # Test foreign key relationships
        self.assertEqual(auth_code.user, self.test_user)
        self.assertEqual(auth_code.client, self.test_client)
        self.assertEqual(token.user, self.test_user)
        self.assertEqual(token.client, self.test_client)

if __name__ == '__main__':
    unittest.main()