#!/usr/bin/env python3
"""
Test script to verify SocketIO functionality
"""

import requests
import sys

def test_socketio_endpoint():
    """Test if the SocketIO endpoint is accessible"""
    try:
        print("🧪 Testing SocketIO endpoint...")
        
        # Test dev environment
        dev_url = "https://dev.unikorn.axfff.com/socket.io/"
        response = requests.get(dev_url, timeout=10)
        
        if response.status_code == 200:
            print("✅ SocketIO endpoint is accessible")
            print(f"   Response: {response.text[:100]}...")
            return True
        else:
            print(f"❌ SocketIO endpoint returned {response.status_code}")
            print(f"   Response: {response.text[:200]}")
            return False
            
    except requests.exceptions.RequestException as e:
        print(f"❌ Failed to connect to SocketIO endpoint: {e}")
        return False

def test_regular_api():
    """Test if regular API endpoints still work"""
    try:
        print("🧪 Testing regular API endpoint...")
        
        # Test a public endpoint
        api_url = "https://dev.unikorn.axfff.com/api/analytics/hot-posts"
        response = requests.get(api_url, timeout=10)
        
        if response.status_code == 200:
            print("✅ Regular API endpoints are working")
            return True
        else:
            print(f"❌ API endpoint returned {response.status_code}")
            return False
            
    except requests.exceptions.RequestException as e:
        print(f"❌ Failed to connect to API endpoint: {e}")
        return False

def main():
    """Run all tests"""
    print("🚀 Testing SocketIO deployment...\n")
    
    success = True
    
    # Test regular API first
    if not test_regular_api():
        success = False
    
    print()
    
    # Test SocketIO endpoint
    if not test_socketio_endpoint():
        success = False
    
    print()
    
    if success:
        print("🎉 All tests passed! SocketIO should be working.")
        return 0
    else:
        print("💥 Some tests failed. Check the deployment configuration.")
        return 1

if __name__ == "__main__":
    sys.exit(main())