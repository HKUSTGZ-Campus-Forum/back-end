#!/usr/bin/env python3
"""
Security verification script for SocketIO deployment
"""

import os
import sys
import requests
from app.config_security import SecurityConfig

def test_cors_security():
    """Test CORS security configuration"""
    print("🔒 Testing CORS Security Configuration...")
    
    env = os.getenv('FLASK_ENV', 'production')
    print(f"Environment: {env}")
    
    allowed_origins = SecurityConfig.get_allowed_origins()
    print(f"Allowed origins: {allowed_origins}")
    
    # Test allowed origins
    success = True
    
    for origin in allowed_origins:
        print(f"✅ Origin '{origin}' should be allowed")
    
    # Test unauthorized origins
    unauthorized_origins = [
        "https://malicious-site.com",
        "http://evil.example.com", 
        "https://unauthorized.domain.com"
    ]
    
    for origin in unauthorized_origins:
        if origin not in allowed_origins:
            print(f"❌ Origin '{origin}' should be REJECTED")
        else:
            print(f"⚠️ WARNING: Unauthorized origin '{origin}' is allowed!")
            success = False
    
    return success

def test_environment_isolation():
    """Test that environments are properly isolated"""
    print("\n🏗️ Testing Environment Isolation...")
    
    env = os.getenv('FLASK_ENV', 'production')
    
    if env == 'production':
        # Production should NOT allow dev/localhost origins
        disallowed = ['http://localhost:3000', 'https://dev.unikorn.axfff.com']
        allowed = SecurityConfig.get_allowed_origins()
        
        for origin in disallowed:
            if origin in allowed:
                print(f"❌ SECURITY RISK: Production allows dev origin '{origin}'")
                return False
            else:
                print(f"✅ Production correctly rejects '{origin}'")
                
    elif env == 'development':
        # Development should allow localhost
        allowed = SecurityConfig.get_allowed_origins()
        required = ['http://localhost:3000', 'https://dev.unikorn.axfff.com']
        
        for origin in required:
            if origin in allowed:
                print(f"✅ Development correctly allows '{origin}'")
            else:
                print(f"❌ Development missing required origin '{origin}'")
                return False
    
    return True

def test_socketio_endpoint():
    """Test SocketIO endpoint based on environment"""
    print("\n🔌 Testing SocketIO Endpoint...")
    
    env = os.getenv('FLASK_ENV', 'production')
    
    if env == 'production':
        url = "https://unikorn.axfff.com/socket.io/"
    else:
        url = "https://dev.unikorn.axfff.com/socket.io/"
    
    try:
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            print(f"✅ SocketIO endpoint accessible: {url}")
            return True
        else:
            print(f"❌ SocketIO endpoint returned {response.status_code}: {url}")
            return False
    except Exception as e:
        print(f"❌ Failed to connect to SocketIO: {e}")
        return False

def main():
    """Run all security tests"""
    print("🚀 SocketIO Security Verification\n")
    
    tests = [
        ("CORS Security", test_cors_security),
        ("Environment Isolation", test_environment_isolation), 
        ("SocketIO Endpoint", test_socketio_endpoint),
    ]
    
    results = []
    for test_name, test_func in tests:
        try:
            result = test_func()
            results.append((test_name, result))
        except Exception as e:
            print(f"❌ {test_name} failed with error: {e}")
            results.append((test_name, False))
    
    print("\n" + "="*50)
    print("📊 Security Test Results:")
    print("="*50)
    
    all_passed = True
    for test_name, passed in results:
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{status}: {test_name}")
        if not passed:
            all_passed = False
    
    print("="*50)
    
    if all_passed:
        print("🎉 All security tests passed!")
        return 0
    else:
        print("💥 Some security tests failed - review configuration!")
        return 1

if __name__ == "__main__":
    sys.exit(main())