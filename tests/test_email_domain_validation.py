#!/usr/bin/env python3
"""
Test HKUST-GZ Email Domain Validation
Quick test script to verify email domain restrictions
"""

import re

def test_hkust_email_validation():
    """Test the HKUST email validation function"""
    
    def is_email(text):
        """Basic email format validation"""
        if not text:
            return False
        email_text = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        return re.match(email_text, text) is not None
    
    def is_hkust_email(email):
        """Check if email belongs to HKUST-GZ domains"""
        if not email:
            return False
        
        email = email.lower().strip()
        
        # Basic email format check first
        if not is_email(email):
            return False
        
        allowed_domains = ['connect.hkust-gz.edu.cn', 'hkust-gz.edu.cn']
        
        for domain in allowed_domains:
            if email.endswith('@' + domain):
                # Additional check to ensure it's exactly the domain, not a subdomain
                email_parts = email.split('@')
                if len(email_parts) == 2 and email_parts[1] == domain:
                    return True
        
        return False

    # Test cases
    test_cases = [
        # Valid HKUST emails
        ("student@connect.hkust-gz.edu.cn", True),
        ("staff@hkust-gz.edu.cn", True),
        ("STUDENT@CONNECT.HKUST-GZ.EDU.CN", True),  # Case insensitive
        ("  teacher@hkust-gz.edu.cn  ", True),       # Whitespace handling
        
        # Invalid emails
        ("student@gmail.com", False),
        ("user@hkust.edu.hk", False),               # Wrong HKUST domain
        ("fake@connect.hkust-gz.edu.cn.evil.com", False),  # Domain spoofing
        ("@connect.hkust-gz.edu.cn", False),        # Missing username
        ("", False),                                # Empty email
        ("not-an-email", False),                   # Not an email format
    ]
    
    print("🧪 Testing HKUST-GZ Email Domain Validation")
    print("=" * 50)
    
    passed = 0
    failed = 0
    
    for email, expected in test_cases:
        result = is_hkust_email(email)
        status = "✅ PASS" if result == expected else "❌ FAIL"
        
        if result == expected:
            passed += 1
        else:
            failed += 1
            
        email_str = str(email) if email is not None else "None"
        print(f"{status} | {email_str:<35} | Expected: {expected!s:<5} | Got: {result!s}")
    
    print("\n" + "=" * 50)
    print(f"📊 Results: {passed} passed, {failed} failed")
    
    if failed == 0:
        print("🎉 All tests passed! Email domain validation is working correctly.")
    else:
        print("⚠️  Some tests failed. Please review the validation logic.")
    
    return failed == 0

if __name__ == "__main__":
    test_hkust_email_validation()