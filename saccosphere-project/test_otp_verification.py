#!/usr/bin/env python
"""
Verification script for OTP implementation.
Tests the three key requirements:
1. DEBUG mode logs OTP code (doesn't send SMS)
2. generate_otp_code() returns 6-character string of digits
3. verify_otp() raises OTPError on wrong code
"""

import os
import sys
import django

# Setup Django
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.development')
django.setup()

from accounts.otp_utils import generate_otp_code, verify_otp, create_otp_token, OTPError
from accounts.models import User, OTPToken
from django.conf import settings

def test_generate_otp_code():
    """Test that generate_otp_code returns 6-character string of digits."""
    print("=== Testing generate_otp_code() ===")
    
    # Generate multiple codes to test consistency
    codes = [generate_otp_code() for _ in range(10)]
    
    for i, code in enumerate(codes):
        print(f"Code {i+1}: {code}")
        
        # Verify it's a string
        if not isinstance(code, str):
            print(f"❌ FAIL: Code {code} is not a string")
            return False
            
        # Verify it's exactly 6 characters
        if len(code) != 6:
            print(f"❌ FAIL: Code {code} is not 6 characters long")
            return False
            
        # Verify all characters are digits
        if not code.isdigit():
            print(f"❌ FAIL: Code {code} contains non-digit characters")
            return False
    
    print("✅ PASS: generate_otp_code() returns 6-character string of digits")
    return True

def test_debug_mode_logging():
    """Test that DEBUG mode logs OTP code instead of sending SMS."""
    print("\n=== Testing DEBUG mode logging ===")
    
    # Ensure DEBUG is True
    original_debug = settings.DEBUG
    settings.DEBUG = True
    
    try:
        from accounts.integrations.otp_service import ATSMSClient, ATSMSError
        
        # Temporarily set API credentials for testing
        original_api_key = getattr(settings, 'AT_API_KEY', '')
        original_username = getattr(settings, 'AT_USERNAME', '')
        
        settings.AT_API_KEY = 'test_key'
        settings.AT_USERNAME = 'test_user'
        
        # Create SMS client
        client = ATSMSClient()
        
        # Test send_otp in DEBUG mode
        result = client.send_otp("254712345678", "123456", "PHONE_VERIFY")
        
        if result is True:
            print("✅ PASS: DEBUG mode returns True without sending SMS")
            print("✅ INFO: Check logs for '[DEBUG MODE] OTP Code for 254712345678 (PHONE_VERIFY): 123456'")
            return True
        else:
            print("❌ FAIL: DEBUG mode should return True")
            return False
            
    except Exception as e:
        print(f"❌ FAIL: Exception in DEBUG mode test: {e}")
        return False
    finally:
        # Restore original settings
        settings.DEBUG = original_debug
        settings.AT_API_KEY = original_api_key
        settings.AT_USERNAME = original_username

def test_verify_otp_wrong_code():
    """Test that verify_otp() raises OTPError on wrong code."""
    print("\n=== Testing verify_otp() with wrong code ===")
    
    try:
        # Get or create a test user
        user, created = User.objects.get_or_create(
            email="test@example.com",
            defaults={
                'first_name': 'Test',
                'last_name': 'User',
            }
        )
        
        # Create an OTP token
        token = create_otp_token(user, "254712345678", "PHONE_VERIFY")
        correct_code = token.code
        print(f"Created OTP token with code: {correct_code}")
        
        # Test with wrong code
        wrong_code = "000000" if correct_code != "000000" else "111111"
        
        try:
            verify_otp("254712345678", wrong_code, "PHONE_VERIFY")
            print("❌ FAIL: verify_otp() should have raised OTPError for wrong code")
            return False
        except OTPError as e:
            print(f"✅ PASS: verify_otp() raised OTPError: {e}")
            return True
        except Exception as e:
            print(f"❌ FAIL: verify_otp() raised wrong exception type: {type(e).__name__}: {e}")
            return False
            
    except Exception as e:
        print(f"❌ FAIL: Exception in wrong code test setup: {e}")
        return False

def main():
    """Run all verification tests."""
    print("OTP Implementation Verification")
    print("=" * 50)
    
    tests = [
        test_generate_otp_code,
        test_debug_mode_logging,
        test_verify_otp_wrong_code,
    ]
    
    results = []
    for test in tests:
        try:
            result = test()
            results.append(result)
        except Exception as e:
            print(f"❌ FAIL: Test {test.__name__} crashed: {e}")
            results.append(False)
    
    print("\n" + "=" * 50)
    print("SUMMARY:")
    
    passed = sum(results)
    total = len(results)
    
    for i, (test, result) in enumerate(zip(tests, results)):
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{i+1}. {test.__name__}: {status}")
    
    print(f"\nOverall: {passed}/{total} tests passed")
    
    if passed == total:
        print("🎉 All verification tests PASSED!")
        return True
    else:
        print("⚠️  Some tests FAILED - review implementation")
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
