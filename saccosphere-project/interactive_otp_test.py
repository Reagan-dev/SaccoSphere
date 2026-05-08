#!/usr/bin/env python
"""
Interactive OTP Test - Enter your phone number and receive OTP
Run this script and follow the prompts to test the OTP service
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

def get_user_for_phone(phone_number):
    """Get or create a user for the given phone number."""
    try:
        # Try to find existing user with this phone
        user = User.objects.filter(phone_number=phone_number).first()
        
        if not user:
            # Create a new user for testing
            email = f"test_{phone_number}@saccosphere.com"
            user = User.objects.create(
                email=email,
                first_name="Test",
                last_name="User",
                phone_number=phone_number
            )
            print(f"Created new test user: {email}")
        else:
            print(f"Using existing user: {user.email}")
        
        return user
    except Exception as e:
        print(f"Error with user: {e}")
        return None

def send_otp_to_phone():
    """Main function to send OTP to user's phone."""
    print("=" * 60)
    print("📱 SEND OTP TO YOUR PHONE")
    print("=" * 60)
    
    # Check configuration
    if settings.DEBUG:
        print("\n⚠️  DEBUG MODE IS ON")
        print("   SMS will NOT be sent - code will be logged instead")
        print("   To send real SMS, set DEBUG=False and add AT_API_KEY to .env")
    
    print(f"\n📋 Current Settings:")
    print(f"   AT_USERNAME: {settings.AT_USERNAME}")
    print(f"   AT_API_KEY: {'SET' if settings.AT_API_KEY else 'NOT SET'}")
    
    # Get phone number from user
    print(f"\n📞 Enter your phone number:")
    phone = input("Phone (e.g., 254712345678 or 0712345678): ").strip()
    
    if not phone:
        print("❌ Phone number is required")
        return
    
    # Get purpose
    print(f"\n🎯 Choose OTP purpose:")
    print("   1. Phone verification")
    print("   2. Password reset")
    print("   3. Login")
    
    purpose_choice = input("Choose (1-3, default=1): ").strip() or "1"
    purpose_map = {
        '1': 'PHONE_VERIFY',
        '2': 'PASSWORD_RESET',
        '3': 'LOGIN'
    }
    
    purpose = purpose_map.get(purpose_choice, 'PHONE_VERIFY')
    print(f"   Purpose: {purpose}")
    
    # Get user
    user = get_user_for_phone(phone)
    if not user:
        print("❌ Cannot proceed without a user")
        return
    
    try:
        # Generate OTP
        print(f"\n🔄 Generating OTP...")
        token = create_otp_token(user, phone, purpose)
        print(f"✅ OTP generated: {token.code}")
        print(f"⏰ Expires at: {token.expires_at.strftime('%Y-%m-%d %H:%M:%S')}")
        
        # Send SMS
        print(f"\n📤 Sending SMS to {phone}...")
        try:
            client = ATSMSClient()
            result = client.send_otp(phone, token.code, purpose)
            
            if result:
                if settings.DEBUG:
                    print("✅ DEBUG MODE: Code logged (no SMS sent)")
                    print(f"📋 Check Django logs for the OTP code")
                else:
                    print("✅ SMS sent successfully!")
                    print(f"📱 Check your phone for the OTP code")
            else:
                print("❌ SMS sending failed")
                
        except ATSMSError as e:
            print(f"❌ SMS Error: {e}")
        except Exception as e:
            print(f"❌ Unexpected error: {e}")
            
        # Offer to verify the OTP
        if input(f"\n🔍 Do you want to verify the OTP now? (y/n): ").lower().startswith('y'):
            verify_otp_interactive(phone, purpose)
            
    except OTPError as e:
        print(f"❌ OTP Error: {e}")
    except Exception as e:
        print(f"❌ Error: {e}")

def verify_otp_interactive(phone, purpose):
    """Interactive OTP verification."""
    print(f"\n🔍 VERIFY OTP")
    print("-" * 30)
    
    code = input("Enter the 6-digit OTP code: ").strip()
    
    if not code:
        print("❌ OTP code is required")
        return
    
    try:
        print(f"🔄 Verifying OTP...")
        token = verify_otp(phone, code, purpose)
        print(f"✅ OTP verified successfully!")
        print(f"📋 Token ID: {token.id}")
        print(f"👤 User: {token.user.email}")
        print(f"📱 Phone: {token.phone_number}")
        
    except OTPError as e:
        print(f"❌ Verification failed: {e}")
    except Exception as e:
        print(f"❌ Error: {e}")

def main_menu():
    """Main menu for interactive testing."""
    while True:
        print("\n" + "=" * 60)
        print("🔐 SACCO SPHERE OTP INTERACTIVE TEST")
        print("=" * 60)
        print("\nChoose an option:")
        print("1. 📱 Send OTP to phone")
        print("2. 🔍 Verify OTP code")
        print("3. ⚙️  Check configuration")
        print("4. 🚪 Exit")
        
        choice = input("\nEnter choice (1-4): ").strip()
        
        if choice == '1':
            send_otp_to_phone()
        elif choice == '2':
            verify_only()
        elif choice == '3':
            check_config()
        elif choice == '4':
            print("\n👋 Goodbye!")
            break
        else:
            print("❌ Invalid choice. Please try again.")

def verify_only():
    """Verify OTP without sending first."""
    print(f"\n🔍 VERIFY OTP ONLY")
    print("-" * 30)
    
    phone = input("Enter phone number: ").strip()
    code = input("Enter 6-digit OTP code: ").strip()
    
    print(f"\n🎯 Choose purpose:")
    print("1. Phone verification")
    print("2. Password reset")
    print("3. Login")
    
    purpose_choice = input("Choose (1-3, default=1): ").strip() or "1"
    purpose_map = {
        '1': 'PHONE_VERIFY',
        '2': 'PASSWORD_RESET',
        '3': 'LOGIN'
    }
    
    purpose = purpose_map.get(purpose_choice, 'PHONE_VERIFY')
    verify_otp_interactive(phone, purpose)

def check_config():
    """Display current configuration."""
    print(f"\n⚙️  CONFIGURATION")
    print("-" * 30)
    print(f"DEBUG mode: {'ON' if settings.DEBUG else 'OFF'}")
    print(f"AT_USERNAME: {settings.AT_USERNAME}")
    print(f"AT_API_KEY: {'SET' if settings.AT_API_KEY else 'NOT SET'}")
    print(f"OTP_EXPIRY_MINUTES: {settings.OTP_EXPIRY_MINUTES}")
    print(f"OTP_MAX_ATTEMPTS: {settings.OTP_MAX_ATTEMPTS}")
    
    if settings.DEBUG:
        print(f"\n📋 To send real SMS:")
        print(f"   1. Add AT_API_KEY to your .env file")
        print(f"   2. Set DEBUG=False")
        print(f"   3. Restart this script")

if __name__ == "__main__":
    try:
        main_menu()
    except KeyboardInterrupt:
        print("\n\n👋 Test interrupted. Goodbye!")
    except Exception as e:
        print(f"\n❌ Test crashed: {e}")
        import traceback
        traceback.print_exc()
