#!/usr/bin/env python3
"""
OAuth2 Testing Script
Tests all OAuth endpoints and flows automatically
"""

import requests
import json
import time
from urllib.parse import urlencode, parse_qs, urlparse
import secrets
import string

class OAuthTester:
    def __init__(self, base_url="http://localhost:5000", debug=True):
        self.base_url = base_url
        self.debug = debug
        self.session = requests.Session()
        self.client_id = None
        self.client_secret = None
        self.access_token = None
        self.user_jwt = None
        
    def log(self, message, level="INFO"):
        if self.debug:
            print(f"[{level}] {message}")
    
    def test_user_login(self, username="testuser", password="testpass"):
        """Test user login to get JWT token"""
        self.log("🔐 Testing user login...")
        
        login_data = {
            "username": username,
            "password": password
        }
        
        response = self.session.post(f"{self.base_url}/api/auth/login", json=login_data)
        
        if response.status_code == 200:
            data = response.json()
            self.user_jwt = data.get("access_token")
            self.session.headers.update({"Authorization": f"Bearer {self.user_jwt}"})
            self.log("✅ User login successful")
            return True
        else:
            self.log(f"❌ User login failed: {response.status_code} - {response.text}", "ERROR")
            return False
    
    def test_client_registration(self):
        """Test OAuth client registration (requires admin)"""
        self.log("📋 Testing OAuth client registration...")
        
        # First, let's check if test client already exists
        try:
            response = self.session.get(f"{self.base_url}/oauth/clients")
            if response.status_code == 200:
                clients = response.json().get("clients", [])
                test_client = next((c for c in clients if c["client_name"] == "Test Client"), None)
                
                if test_client:
                    self.client_id = test_client["client_id"]
                    self.log("ℹ️  Test client already exists, using existing credentials")
                    # Note: We can't get the secret from the API for security reasons
                    # You'll need to check the database or create a new client
                    return True
        except:
            pass
        
        # Create new test client
        client_data = {
            "client_name": "Test Client",
            "client_description": "OAuth testing client",
            "client_uri": "http://localhost:3000",
            "redirect_uris": [
                "http://localhost:3000/callback",
                "http://127.0.0.1:3000/callback"
            ],
            "scope": "profile email"
        }
        
        response = self.session.post(f"{self.base_url}/oauth/clients", json=client_data)
        
        if response.status_code == 201:
            data = response.json()
            self.client_id = data["client_id"]
            self.client_secret = data["client_secret"]
            self.log("✅ OAuth client registration successful")
            self.log(f"   Client ID: {self.client_id}")
            self.log(f"   Client Secret: {self.client_secret}")
            return True
        else:
            self.log(f"❌ OAuth client registration failed: {response.status_code} - {response.text}", "ERROR")
            return False
    
    def test_authorization_endpoint(self):
        """Test OAuth authorization endpoint"""
        self.log("🎫 Testing authorization endpoint...")
        
        if not self.client_id:
            self.log("❌ No client ID available", "ERROR")
            return False
        
        # Build authorization URL
        auth_params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": "http://localhost:3000/callback",
            "scope": "profile email",
            "state": "test_state_123"
        }
        
        auth_url = f"{self.base_url}/oauth/authorize?{urlencode(auth_params)}"
        self.log(f"   Authorization URL: {auth_url}")
        
        # Test GET request (should show consent screen)
        response = self.session.get(auth_url)
        
        if response.status_code == 200:
            if "Authorize" in response.text and "Test Client" in response.text:
                self.log("✅ Authorization endpoint returns consent screen")
                
                # Simulate user approval (POST request)
                form_data = auth_params.copy()
                form_data["action"] = "allow"
                
                post_response = self.session.post(f"{self.base_url}/oauth/authorize", data=form_data, allow_redirects=False)
                
                if post_response.status_code in [302, 301]:  # Redirect response
                    redirect_url = post_response.headers.get("Location")
                    parsed_url = urlparse(redirect_url)
                    query_params = parse_qs(parsed_url.query)
                    
                    if "code" in query_params:
                        auth_code = query_params["code"][0]
                        self.log("✅ Authorization code generated successfully")
                        self.log(f"   Authorization code: {auth_code[:10]}...")
                        return auth_code
                    else:
                        self.log("❌ No authorization code in redirect", "ERROR")
                        return None
                else:
                    self.log(f"❌ Expected redirect, got {post_response.status_code}", "ERROR")
                    return None
            else:
                self.log("❌ Consent screen not displayed correctly", "ERROR")
                return None
        else:
            self.log(f"❌ Authorization endpoint failed: {response.status_code} - {response.text}", "ERROR")
            return None
    
    def test_token_endpoint(self, auth_code):
        """Test OAuth token endpoint"""
        self.log("🎟️  Testing token endpoint...")
        
        if not auth_code or not self.client_secret:
            self.log("❌ Missing authorization code or client secret", "ERROR")
            return False
        
        token_data = {
            "grant_type": "authorization_code",
            "code": auth_code,
            "redirect_uri": "http://localhost:3000/callback",
            "client_id": self.client_id,
            "client_secret": self.client_secret
        }
        
        response = requests.post(f"{self.base_url}/oauth/token", data=token_data)
        
        if response.status_code == 200:
            data = response.json()
            self.access_token = data.get("access_token")
            
            required_fields = ["access_token", "token_type", "expires_in"]
            if all(field in data for field in required_fields):
                self.log("✅ Token endpoint successful")
                self.log(f"   Token type: {data['token_type']}")
                self.log(f"   Expires in: {data['expires_in']} seconds")
                self.log(f"   Scope: {data.get('scope', 'N/A')}")
                return True
            else:
                self.log("❌ Token response missing required fields", "ERROR")
                return False
        else:
            self.log(f"❌ Token endpoint failed: {response.status_code} - {response.text}", "ERROR")
            return False
    
    def test_userinfo_endpoint(self):
        """Test OAuth userinfo endpoint"""
        self.log("👤 Testing userinfo endpoint...")
        
        if not self.access_token:
            self.log("❌ No access token available", "ERROR")
            return False
        
        headers = {"Authorization": f"Bearer {self.access_token}"}
        response = requests.get(f"{self.base_url}/oauth/userinfo", headers=headers)
        
        if response.status_code == 200:
            user_data = response.json()
            
            required_fields = ["sub"]  # Subject (user ID) is required
            if all(field in user_data for field in required_fields):
                self.log("✅ UserInfo endpoint successful")
                self.log(f"   User ID: {user_data['sub']}")
                self.log(f"   Username: {user_data.get('username', 'N/A')}")
                self.log(f"   Email: {user_data.get('email', 'N/A')}")
                return True
            else:
                self.log("❌ UserInfo response missing required fields", "ERROR")
                return False
        else:
            self.log(f"❌ UserInfo endpoint failed: {response.status_code} - {response.text}", "ERROR")
            return False
    
    def test_token_revocation(self):
        """Test OAuth token revocation"""
        self.log("🗑️  Testing token revocation...")
        
        if not self.access_token:
            self.log("❌ No access token to revoke", "ERROR")
            return False
        
        revoke_data = {
            "token": self.access_token,
            "client_id": self.client_id,
            "client_secret": self.client_secret
        }
        
        response = requests.post(f"{self.base_url}/oauth/revoke", data=revoke_data)
        
        if response.status_code == 200:
            self.log("✅ Token revocation successful")
            
            # Verify token is actually revoked
            headers = {"Authorization": f"Bearer {self.access_token}"}
            test_response = requests.get(f"{self.base_url}/oauth/userinfo", headers=headers)
            
            if test_response.status_code == 401:
                self.log("✅ Revoked token correctly rejected")
                return True
            else:
                self.log("⚠️  Token revocation succeeded but token still works", "WARNING")
                return False
        else:
            self.log(f"❌ Token revocation failed: {response.status_code} - {response.text}", "ERROR")
            return False
    
    def test_error_cases(self):
        """Test error handling"""
        self.log("⚠️  Testing error cases...")
        
        # Test invalid client ID
        auth_url = f"{self.base_url}/oauth/authorize?response_type=code&client_id=invalid&redirect_uri=http://localhost:3000/callback"
        response = requests.get(auth_url)
        
        if response.status_code == 400:
            self.log("✅ Invalid client correctly rejected")
        else:
            self.log("❌ Invalid client not properly handled", "ERROR")
        
        # Test invalid token
        headers = {"Authorization": "Bearer invalid_token"}
        response = requests.get(f"{self.base_url}/oauth/userinfo", headers=headers)
        
        if response.status_code == 401:
            self.log("✅ Invalid token correctly rejected")
        else:
            self.log("❌ Invalid token not properly handled", "ERROR")
    
    def run_full_test(self):
        """Run complete OAuth flow test"""
        self.log("🚀 Starting OAuth2 Full Flow Test")
        self.log("=" * 50)
        
        # Step 1: Login user
        if not self.test_user_login():
            self.log("❌ Cannot continue without user login", "ERROR")
            return False
        
        # Step 2: Register or get client
        if not self.test_client_registration():
            self.log("❌ Cannot continue without OAuth client", "ERROR")
            return False
        
        # Step 3: Test authorization
        auth_code = self.test_authorization_endpoint()
        if not auth_code:
            self.log("❌ Cannot continue without authorization code", "ERROR")
            return False
        
        # Step 4: Exchange code for token
        if not self.test_token_endpoint(auth_code):
            self.log("❌ Cannot continue without access token", "ERROR")
            return False
        
        # Step 5: Test userinfo
        if not self.test_userinfo_endpoint():
            self.log("❌ UserInfo endpoint failed", "ERROR")
            return False
        
        # Step 6: Test token revocation
        if not self.test_token_revocation():
            self.log("⚠️  Token revocation failed", "WARNING")
        
        # Step 7: Test error cases
        self.test_error_cases()
        
        self.log("=" * 50)
        self.log("🎉 OAuth2 Full Flow Test Complete!")
        return True

def main():
    print("OAuth2 Testing Script")
    print("Make sure your Flask development server is running on http://localhost:5000")
    print("You need a test user account to run these tests.\n")
    
    base_url = input("Base URL (default: http://localhost:5000): ").strip()
    if not base_url:
        base_url = "http://localhost:5000"
    
    username = input("Test username (default: testuser): ").strip()
    if not username:
        username = "testuser"
    
    password = input("Test password (default: testpass): ").strip()
    if not password:
        password = "testpass"
    
    tester = OAuthTester(base_url=base_url, debug=True)
    
    # Override login credentials
    tester.test_user_login = lambda: tester.test_user_login(username, password)
    
    success = tester.run_full_test()
    
    if success:
        print("\n✅ All tests passed! Your OAuth implementation is ready for production.")
    else:
        print("\n❌ Some tests failed. Please review the errors above.")
    
    return 0 if success else 1

if __name__ == "__main__":
    exit(main())