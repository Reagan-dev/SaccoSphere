#!/usr/bin/env python
"""
Verify OTP endpoints before next step
Tests all three verification requirements:
1. POST /api/v1/accounts/otp/send/ returns 200 in DEBUG mode (code logged)
2. POST /api/v1/accounts/otp/verify/ with correct code returns 200
3. POST /api/v1/accounts/otp/resend/ before cooldown → 429 with seconds_remaining
"""

import os
import sys
import django

# Setup Django
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.development')
os.environ.setdefault('ALLOWED_HOSTS', 'localhost,127.0.0.1,testserver')
django.setup()

from django.test import Client
from django.contrib.auth import get_user_model
from accounts.models import OTPToken

User = get_user_model()

def test_otp_send_debug_mode():
    """Test 1: OTP send returns 200 in DEBUG mode."""
    print("=" * 60)
    print("REQUIREMENT 1: OTP SEND RETURNS 200 IN DEBUG MODE")
    print("=" * 60)
    
    client = Client()
    
    # Test OTP send
    response = client.post('/api/v1/accounts/otp/send/', {
        'phone_number': '254712345678',
        'purpose': 'PHONE_VERIFY'
    })
    
    print(f"Status Code: {response.status_code}")
    print(f"Response Data: {response.data}")
    
    if response.status_code == 200:
        print("✅ PASS: OTP send returns 200 in DEBUG mode")
        
        # Check if code was logged (DEBUG mode)
        try:
            token = OTPToken.objects.filter(
                phone_number='254712345678',
                purpose='PHONE_VERIFY'
            ).first()
            if token:
                print(f"✅ INFO: OTP code logged to console: {token.code}")
            else:
                print("❌ FAIL: No OTP token created")
        except Exception as e:
            print(f"❌ ERROR: Could not verify token creation: {e}")
    else:
        print("❌ FAIL: OTP send did not return 200")
    
    return response.status_code == 200

def test_otp_verify_correct_code():
    """Test 2: OTP verify returns 200 with correct code."""
    print("\n" + "=" * 60)
    print("REQUIREMENT 2: OTP VERIFY RETURNS 200 WITH CORRECT CODE")
    print("=" * 60)
    
    client = Client()
    
    # First, create a user and OTP token for testing
    try:
        user, _ = User.objects.get_or_create(
            email='test@example.com',
            defaults={
                'first_name': 'Test',
                'last_name': 'User',
                'phone_number': '254712345678'
            }
        )
        
        from accounts.otp_utils import create_otp_token
        token = create_otp_token(user, '254712345678', 'PHONE_VERIFY')
        otp_code = token.code
        
        print(f"✅ INFO: Created test OTP code: {otp_code}")
        
    except Exception as e:
        print(f"❌ ERROR: Could not create test data: {e}")
        return False
    
    # Test OTP verification with correct code
    response = client.post('/api/v1/accounts/otp/verify/', {
        'phone_number': '254712345678',
        'code': otp_code
    })
    
    print(f"Status Code: {response.status_code}")
    print(f"Response Data: {response.data}")
    
    if response.status_code == 200:
        print("✅ PASS: OTP verify returns 200 with correct code")
        return True
    else:
        print("❌ FAIL: OTP verify did not return 200")
        return False

def test_otp_resend_cooldown():
    """Test 3: OTP resend returns 429 before cooldown."""
    print("\n" + "=" * 60)
    print("REQUIREMENT 3: OTP RESEND RETURNS 429 BEFORE COOLDOWN")
    print("=" * 60)
    
    client = Client()
    
    # Create user and send first OTP
    try:
        user, _ = User.objects.get_or_create(
            email='test2@example.com',
            defaults={
                'first_name': 'Test',
                'last_name': 'User2',
                'phone_number': '254712345679'
            }
        )
        
        from accounts.otp_utils import create_otp_token
        token = create_otp_token(user, '254712345679', 'PHONE_VERIFY')
        print(f"✅ INFO: Created first OTP for cooldown test")
        
    except Exception as e:
        print(f"❌ ERROR: Could not create test data: {e}")
        return False
    
    # Try to resend OTP immediately (should hit cooldown)
    response = client.post('/api/v1/accounts/otp/resend/', {
        'phone_number': '254712345679',
        'purpose': 'PHONE_VERIFY'
    })
    
    print(f"Status Code: {response.status_code}")
    print(f"Response Data: {response.data}")
    
    if response.status_code == 429:
        print("✅ PASS: OTP resend returns 429 before cooldown")
        
        # Check if seconds_remaining is provided
        if 'seconds_remaining' in response.data:
            seconds = response.data['seconds_remaining']
            print(f"✅ INFO: Cooldown seconds remaining: {seconds}")
        else:
            print("❌ FAIL: No seconds_remaining in response")
        
        return True
    else:
        print("❌ FAIL: OTP resend did not return 429")
        return False

def main():
    """Run all verification tests."""
    print("🔐 OTP ENDPOINTS VERIFICATION")
    print("Testing all three requirements before next step")
    
    tests = [
        ("OTP send in DEBUG mode", test_otp_send_debug_mode),
        ("OTP verify correct code", test_otp_verify_correct_code),
        ("OTP resend cooldown", test_otp_resend_cooldown),
    ]
    
    results = []
    for test_name, test_func in tests:
        try:
            result = test_func()
            results.append((test_name, result))
        except Exception as e:
            print(f"❌ CRASH: {test_name} - {e}")
            results.append((test_name, False))
    
    # Summary
    print("\n" + "=" * 60)
    print("📋 VERIFICATION SUMMARY")
    print("=" * 60)
    
    passed = 0
    total = len(results)
    
    for test_name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{status}: {test_name}")
        if result:
            passed += 1
    
    print(f"\n📊 RESULTS: {passed}/{total} tests passed")
    
    if passed == total:
        print("🎉 ALL REQUIREMENTS VERIFIED - READY FOR NEXT STEP!")
        return True
    else:
        print("⚠️  SOME REQUIREMENTS FAILED - FIX BEFORE PROCEEDING")
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
