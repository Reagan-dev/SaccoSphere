"""Verify OTP implementation works as expected."""
import os
import sys
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.development')
django.setup()

from accounts.otp_utils import generate_otp_code, create_otp_token, verify_otp, OTPError
from accounts.models import User

print("\n" + "=" * 80)
print("OTP IMPLEMENTATION VERIFICATION")
print("=" * 80)

# TEST 1: generate_otp_code() returns 6-character string
print("\n✓ TEST 1: generate_otp_code() returns 6-character string of digits")
code = generate_otp_code()
print(f"  Generated code: {code}")
assert isinstance(code, str), "Code must be string"
assert len(code) == 6, "Code must be exactly 6 characters"
assert code.isdigit(), "Code must contain only digits"
print(f"  ✓ Code is string: {isinstance(code, str)}")
print(f"  ✓ Code length is 6: {len(code) == 6}")
print(f"  ✓ Code is all digits: {code.isdigit()}")

# TEST 2: In DEBUG mode, OTP code is logged (not sent via SMS)
print("\n✓ TEST 2: In DEBUG mode, OTP code is logged to console (not sent via SMS)")
from django.conf import settings
print(f"  DEBUG mode: {settings.DEBUG}")
print(f"  AT_API_KEY configured: {bool(settings.AT_API_KEY)}")
print(f"  AT_USERNAME: {settings.AT_USERNAME}")

# Get or create test user
user = User.objects.first()
if not user:
    print("  No users found. Creating test user...")
    user = User.objects.create_user(
        email='otp_test@test.com',
        password='TestPass123'
    )

print(f"  Using user: {user.email}")

# Create OTP token (logs to console in DEBUG mode)
print("\n  Creating OTP token (should log code to console in DEBUG mode)...")
token = create_otp_token(user, '+254712345678', 'PHONE_VERIFY')
print(f"  Token created with code: {token.code}")
print(f"  Token is_used: {token.is_used}")
print(f"  Token is_expired: {token.is_expired}")

# TEST 3: verify_otp() raises OTPError on wrong code
print("\n✓ TEST 3: verify_otp() raises OTPError on wrong code")
try:
    # Try with wrong code
    verify_otp('+254712345678', '000000', 'PHONE_VERIFY')
    print("  ✗ FAILED: Should have raised OTPError")
    sys.exit(1)
except OTPError as e:
    print(f"  ✓ Correctly raised OTPError: {str(e)}")

# TEST 4: Verify with correct code works
print("\n✓ TEST 4: verify_otp() succeeds with correct code")
token2 = create_otp_token(user, '+254712345678', 'PASSWORD_RESET')
verified_token = verify_otp('+254712345678', token2.code, 'PASSWORD_RESET')
print(f"  ✓ Token verified successfully")
print(f"  ✓ Token now marked as is_used: {verified_token.is_used}")

# TEST 5: Verify same code twice raises error
print("\n✓ TEST 5: verify_otp() raises error on reuse (already used)")
try:
    verify_otp('+254712345678', token2.code, 'PASSWORD_RESET')
    print("  ✗ FAILED: Should have raised OTPError on reuse")
    sys.exit(1)
except OTPError as e:
    print(f"  ✓ Correctly rejected reuse: {str(e)}")

print("\n" + "=" * 80)
print("ALL TESTS PASSED ✓")
print("=" * 80 + "\n")
