#!/usr/bin/env python
"""
Manual OTP Service Test
Interactive script to test the complete OTP flow:
1. Enter phone number
2. Generate and send OTP
3. Enter received code
4. Verify the code
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

def get_or_create_test_user():
    """Get or create a test user for OTP testing."""
    try:
        user = User.objects.first()
        if not user:
            print("Creating test user...")
            user = User.objects.create(
                email="test@saccosphere.com",
                first_name="Test",
                last_name="User",
                phone_number="254712345678"
            )
        return user
    except Exception as e:
        print(f"Error getting/creating user: {e}")
        return None

def test_otp_flow():
    """Interactive OTP flow test."""
    print("=" * 60)
    print("🔐 SACCO SPHERE OTP SERVICE MANUAL TEST")
    print("=" * 60)
    
    # Check if we have API credentials
    if not settings.AT_API_KEY or settings.AT_USERNAME == 'sandbox':
        print("\n📋 NOTE: Running in DEBUG/sandbox mode")
        print("   - SMS will NOT be sent")
        print("   - OTP codes will be logged to console")
        print("   - Set real AT_API_KEY in .env for actual SMS")
    
    # Get test user
    user = get_or_create_test_user()
    if not user:
        print("❌ Cannot proceed without a user")
        return
    
    print(f"\n👤 Test User: {user.email}")
    
    while True:
        print("\n" + "-" * 40)
        print("Choose an option:")
        print("1. Send OTP to phone number")
        print("2. Verify OTP code")
        print("3. Check current DEBUG status")
        print("4. Exit")
        print("-" * 40)
        
        choice = input("Enter choice (1-4): ").strip()
        
        if choice == '1':
            send_otp_interactive(user)
        elif choice == '2':
            verify_otp_interactive()
        elif choice == '3':
            check_debug_status()
        elif choice == '4':
            print("\n👋 Goodbye!")
            break
        else:
            print("❌ Invalid choice. Please try again.")

def send_otp_interactive(user):
    """Handle interactive OTP sending."""
    print("\n📱 Send OTP")
    print("-" * 20)
    
    # Get phone number
    phone = input("Enter phone number (e.g., 254712345678 or 0712345678): ").strip()
    
    if not phone:
        print("❌ Phone number is required")
        return
    
    # Get purpose
    print("\nOTP Purpose:")
    print("1. PHONE_VERIFY")
    print("2. PASSWORD_RESET") 
    print("3. LOGIN")
    
    purpose_choice = input("Choose purpose (1-3): ").strip()
    purpose_map = {
        '1': 'PHONE_VERIFY',
        '2': 'PASSWORD_RESET',
        '3': 'LOGIN'
    }
    
    purpose = purpose_map.get(purpose_choice, 'PHONE_VERIFY')
    
    try:
        # Generate OTP
        print(f"\n🔄 Generating OTP for {phone} ({purpose})...")
        token = create_otp_token(user, phone, purpose)
        print(f"✅ OTP generated: {token.code}")
        print(f"⏰ Expires at: {token.expires_at}")
        
        # Try to send SMS
        print(f"\n📤 Sending SMS...")
        try:
            client = ATSMSClient()
            result = client.send_otp(phone, token.code, purpose)
            
            if result:
                if settings.DEBUG:
                    print("✅ DEBUG mode: Code logged (no SMS sent)")
                else:
                    print("✅ SMS sent successfully!")
            else:
                print("❌ SMS sending failed")
                
        except ATSMSError as e:
            print(f"❌ SMS Error: {e}")
        except Exception as e:
            print(f"❌ Unexpected error: {e}")
            
    except OTPError as e:
        print(f"❌ OTP Error: {e}")
    except Exception as e:
        print(f"❌ Error: {e}")

def verify_otp_interactive():
    """Handle interactive OTP verification."""
    print("\n🔍 Verify OTP")
    print("-" * 20)
    
    # Get phone number
    phone = input("Enter phone number: ").strip()
    code = input("Enter 6-digit OTP code: ").strip()
    
    # Get purpose
    print("\nOTP Purpose:")
    print("1. PHONE_VERIFY")
    print("2. PASSWORD_RESET") 
    print("3. LOGIN")
    
    purpose_choice = input("Choose purpose (1-3): ").strip()
    purpose_map = {
        '1': 'PHONE_VERIFY',
        '2': 'PASSWORD_RESET',
        '3': 'LOGIN'
    }
    
    purpose = purpose_map.get(purpose_choice, 'PHONE_VERIFY')
    
    try:
        print(f"\n🔄 Verifying OTP...")
        token = verify_otp(phone, code, purpose)
        print(f"✅ OTP verified successfully!")
        print(f"📋 Token ID: {token.id}")
        print(f"👤 User: {token.user.email}")
        
    except OTPError as e:
        print(f"❌ Verification failed: {e}")
    except Exception as e:
        print(f"❌ Error: {e}")

def check_debug_status():
    """Check and display current DEBUG mode status."""
    print(f"\n🔧 DEBUG Status")
    print("-" * 20)
    print(f"DEBUG mode: {'ON' if settings.DEBUG else 'OFF'}")
    print(f"AT_USERNAME: {settings.AT_USERNAME}")
    print(f"AT_API_KEY: {'SET' if settings.AT_API_KEY else 'NOT SET'}")
    print(f"OTP_EXPIRY_MINUTES: {settings.OTP_EXPIRY_MINUTES}")
    print(f"OTP_MAX_ATTEMPTS: {settings.OTP_MAX_ATTEMPTS}")

if __name__ == "__main__":
    try:
        test_otp_flow()
    except KeyboardInterrupt:
        print("\n\n👋 Test interrupted. Goodbye!")
    except Exception as e:
        print(f"\n❌ Test crashed: {e}")
        import traceback
        traceback.print_exc()
