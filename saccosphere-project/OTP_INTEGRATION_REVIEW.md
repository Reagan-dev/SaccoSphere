# ============================================================
# REVIEW — READ THIS THEN DELETE FROM THIS LINE TO THE END
# ============================================================

## accounts/integrations/otp_service.py

**ATSMSClient class**: This is the main SMS service client that handles sending OTP codes via Africa's Talking API. 

- **__init__ method**: Reads AT_API_KEY and AT_USERNAME from Django settings, initializes the Africa's Talking SDK, and sets up the SMS service. This ensures the service is ready to send messages when needed.

- **_normalize_phone method**: Converts phone numbers to the standard 254XXXXXXXXX format that Africa's Talking expects. It handles different input formats like +254712345678, 254712345678, or 0712345678 and normalizes them consistently.

- **send_otp method**: The core function that sends OTP codes. It builds appropriate message templates for different purposes (phone verification, password reset, login), sends the SMS via Africa's Talking, logs the result, and handles errors. In DEBUG mode, it logs the code instead of actually sending SMS to save costs during development.

**ATSMSError exception**: Custom exception that makes it easier to catch and handle SMS-specific errors in your views.

## accounts/integrations/__init__.py

This empty file makes the integrations directory a proper Python package, allowing you to import the otp_service module with `from accounts.integrations.otp_service import ATSMSClient`.

## config/settings/base.py

The added settings configure the OTP system:

- **AT_API_KEY and AT_USERNAME**: Africa's Talking API credentials. The username defaults to 'sandbox' for development.
- **OTP_EXPIRY_MINUTES**: How long OTP codes remain valid (5 minutes).
- **OTP_MAX_ATTEMPTS**: Maximum failed verification attempts before blocking (3 attempts).
- **OTP_RESEND_COOLDOWN_SECONDS**: Minimum time between OTP resend requests (60 seconds).

## accounts/otp_utils.py

**generate_otp function**: Creates cryptographically secure 6-digit codes using secrets.randbelow() instead of random.randint(). This is important for security because secrets module provides cryptographically strong random numbers suitable for security-sensitive applications.

**create_otp_token function**: Manages OTP token lifecycle. It expires any existing active tokens for the same user+purpose combination (preventing multiple valid codes), generates a new secure code, sets the expiry time, and creates the database record. This ensures only one valid OTP exists per user per purpose at any time.

**verify_otp function**: Validates submitted OTP codes. It finds the matching unused token, checks if it's expired, increments attempt counters, validates the code, and marks it as used. This prevents code reuse and tracks failed attempts for security.

**OTPError exception**: Custom exception for OTP-related errors, making error handling cleaner in your views.

## accounts/serializers.py

**OTPRequestSerializer**: Validates OTP request data. It ensures phone numbers are in valid Kenyan E.164 format and that the purpose is one of the allowed choices (PHONE_VERIFY, PASSWORD_RESET, LOGIN).

**OTPVerifySerializer**: Validates OTP verification data. It validates the phone number format and ensures the code is exactly 6 characters long, providing basic input validation before processing.

## Django/Python concepts you might not know well

**secrets module**: This is Python's module for generating cryptographically strong random numbers. Unlike random.randint(), secrets.randbelow() is suitable for security-sensitive applications like OTP codes because it's unpredictable and resistant to timing attacks.

**E.164 format**: This is the international standard for phone numbers. Kenyan numbers in E.164 format start with +254 followed by 9 digits (e.g., +254712345678). Your validation accepts both +254 and 254 formats for flexibility.

**Django settings with config()**: The decouple library's config() function reads environment variables with fallback defaults. This allows different configurations for development, staging, and production without changing code.

**Database query optimization**: The verify_otp function uses expires_at__gt=timezone.now() to filter expired tokens at the database level rather than fetching them and checking in Python. This is more efficient.

## Manual test to confirm it works

1. Set DEBUG=True in your settings and add AT_API_KEY and AT_USERNAME to your .env file.
2. Open Django shell: `python manage.py shell`
3. Test the service:
```python
from accounts.integrations.otp_service import ATSMSClient
from accounts.otp_utils import generate_otp_code, create_otp_token, verify_otp
from accounts.models import User

# Get a test user
user = User.objects.first()

# Create an OTP token
token = create_otp_token(user, '254712345678', 'PHONE_VERIFY')
print(f'Generated OTP: {token.code}')

# Verify the OTP
verified = verify_otp('254712345678', token.code, 'PHONE_VERIFY')
print(f'Verified: {verified.is_used}')  # Should be True
```

4. Check your Django logs to see the DEBUG mode OTP logging instead of actual SMS sending.

## Important design decisions and why

**DEBUG mode SMS bypass**: The service logs OTP codes instead of sending SMS when DEBUG=True. This saves money during development and testing while still exercising the full code path.

**Single active OTP per user+purpose**: The create_otp_token function expires existing tokens before creating new ones. This prevents confusion where users have multiple valid codes and reduces security risks.

**Cryptographically secure codes**: Using secrets.randbelow() instead of random.randint() ensures OTP codes are unpredictable and can't be guessed through pattern analysis.

**Phone normalization**: Centralizing phone number formatting in the SMS service ensures consistency and prevents formatting errors that could cause SMS delivery failures.

**Attempt tracking**: The system tracks failed attempts and blocks after too many tries, preventing brute force attacks on OTP codes.

**Purpose-specific messages**: Different message templates for different OTP purposes provide clearer user experience while maintaining consistent branding.

# END OF REVIEW — DELETE EVERYTHING FROM THE FIRST # LINE ABOVE
# ============================================================
