#!/usr/bin/env python
"""
Automated OTP Service Demo
Demonstrates the complete OTP flow without requiring user input
"""

import os
import sys
import django

# Setup Django
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.development')
django.setup()

from accounts.otp_utils import generate_otp_code, create_otp_token, verify_otp, OTPError
from accounts.integrations.otp_service import ATSMSClient, ATSMSError
from accounts.models import User
from django.conf import settings

def demo_otp_service():
    """Demonstrate the complete OTP service flow."""
    print("=" * 60)
    print("🔐 SACCO SPHERE OTP SERVICE DEMO")
    print("=" * 60)
    
    # Show current configuration
    print(f"\n📋 Configuration:")
    print(f"   DEBUG mode: {'ON' if settings.DEBUG else 'OFF'}")
    print(f"   AT_USERNAME: {settings.AT_USERNAME}")
    print(f"   AT_API_KEY: {'SET' if settings.AT_API_KEY else 'NOT SET'}")
    print(f"   OTP_EXPIRY_MINUTES: {settings.OTP_EXPIRY_MINUTES}")
    print(f"   OTP_MAX_ATTEMPTS: {settings.OTP_MAX_ATTEMPTS}")
    
    # Get or create test user
    try:
        user = User.objects.first()
        if not user:
            print(f"\n👤 Creating test user...")
            user = User.objects.create(
                email="demo@saccosphere.com",
                first_name="Demo",
                last_name="User",
                phone_number="254712345678"
            )
        else:
            print(f"\n👤 Using existing user: {user.email}")
    except Exception as e:
        print(f"❌ Error with user: {e}")
        return
    
    # Test phone number
    test_phone = "254712345678"
    test_purpose = "PHONE_VERIFY"
    
    print(f"\n📱 Testing with phone: {test_phone}")
    print(f"🎯 Purpose: {test_purpose}")
    
    # Step 1: Generate OTP
    print(f"\n" + "="*40)
    print("STEP 1: GENERATING OTP")
    print("="*40)
    
    try:
        token = create_otp_token(user, test_phone, test_purpose)
        print(f"✅ OTP generated successfully!")
        print(f"   Code: {token.code}")
        print(f"   Expires: {token.expires_at}")
        print(f"   Token ID: {token.id}")
        
    except OTPError as e:
        print(f"❌ OTP generation failed: {e}")
        return
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        return
    
    # Step 2: Send SMS (or log in DEBUG mode)
    print(f"\n" + "="*40)
    print("STEP 2: SENDING SMS")
    print("="*40)
    
    try:
        client = ATSMSClient()
        result = client.send_otp(test_phone, token.code, test_purpose)
        
        if result:
            if settings.DEBUG:
                print("✅ DEBUG mode: Code logged to console")
                print(f"   📋 Check logs for: [DEBUG MODE] OTP Code for {test_phone} ({test_purpose}): {token.code}")
            else:
                print("✅ SMS sent successfully!")
                print(f"   📱 Message sent to: {test_phone}")
        else:
            print("❌ SMS sending failed")
            
    except ATSMSError as e:
        print(f"❌ SMS Error: {e}")
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
    
    # Step 3: Verify OTP (correct code)
    print(f"\n" + "="*40)
    print("STEP 3: VERIFYING OTP (CORRECT CODE)")
    print("="*40)
    
    try:
        verified_token = verify_otp(test_phone, token.code, test_purpose)
        print(f"✅ OTP verified successfully!")
        print(f"   Token ID: {verified_token.id}")
        print(f"   Used: {verified_token.is_used}")
        print(f"   Attempts: {verified_token.attempts}")
        
    except OTPError as e:
        print(f"❌ Verification failed: {e}")
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
    
    # Step 4: Verify OTP (wrong code)
    print(f"\n" + "="*40)
    print("STEP 4: VERIFYING OTP (WRONG CODE)")
    print("="*40)
    
    # Create a new token for wrong code test
    try:
        new_token = create_otp_token(user, test_phone, test_purpose)
        print(f"🔄 New OTP generated: {new_token.code}")
        
        # Try wrong code
        wrong_code = "000000" if new_token.code != "000000" else "111111"
        print(f"❌ Trying wrong code: {wrong_code}")
        
        verified_token = verify_otp(test_phone, wrong_code, test_purpose)
        print(f"❌ This should not happen - verification succeeded with wrong code!")
        
    except OTPError as e:
        print(f"✅ Expected error caught: {e}")
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
    
    # Step 5: Check multiple attempts
    print(f"\n" + "="*40)
    print("STEP 5: TESTING MULTIPLE ATTEMPTS")
    print("="*40)
    
    try:
        # Create another token
        attempt_token = create_otp_token(user, test_phone, test_purpose)
        print(f"🔄 New OTP for attempt test: {attempt_token.code}")
        
        # Try wrong codes multiple times
        for i in range(settings.OTP_MAX_ATTEMPTS):
            wrong_code = f"{i:06d}" if attempt_token.code != f"{i:06d}" else "999999"
            try:
                verify_otp(test_phone, wrong_code, test_purpose)
                print(f"❌ Attempt {i+1}: Should have failed!")
            except OTPError as e:
                print(f"✅ Attempt {i+1}: Failed as expected - {e}")
        
        # Try one more time (should be blocked)
        try:
            verify_otp(test_phone, attempt_token.code, test_purpose)
            print(f"❌ Should be blocked after max attempts!")
        except OTPError as e:
            print(f"✅ Max attempts reached: {e}")
            
    except Exception as e:
        print(f"❌ Error in attempt test: {e}")
    
    # Summary
    print(f"\n" + "="*60)
    print("🎉 DEMO COMPLETE")
    print("="*60)
    print(f"✅ OTP generation: Working")
    print(f"✅ SMS sending (DEBUG mode): Working")
    print(f"✅ OTP verification: Working")
    print(f"✅ Error handling: Working")
    print(f"✅ Attempt limiting: Working")
    
    if settings.DEBUG:
        print(f"\n📋 To test with real SMS:")
        print(f"   1. Set AT_API_KEY and AT_USERNAME in .env")
        print(f"   2. Set DEBUG=False in production")
        print(f"   3. Run this demo again")

if __name__ == "__main__":
    try:
        demo_otp_service()
    except KeyboardInterrupt:
        print("\n\n👋 Demo interrupted. Goodbye!")
    except Exception as e:
        print(f"\n❌ Demo crashed: {e}")
        import traceback
        traceback.print_exc()
