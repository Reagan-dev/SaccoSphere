"""OTP code generation and verification utilities."""
import hashlib
import hmac
import logging
import secrets
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger('saccosphere.otp')


def format_phone_number(phone_number):
    """
    Normalize a Kenyan phone number to canonical E.164 format (+254XXXXXXXXX).

    Accepted input shapes (all normalize to +254712345678 for the example):
    - '+254712345678' (13 digits with country code and plus)
    - '254712345678' (12 digits with country code, no plus)
    - '0712345678' (10 digits with leading 0)
    - '0112345678' (10 digits with leading 0, newer prefixes)
    - '712345678' (9 digits without leading 0 or country code)
    - '112345678' (9 digits without leading 0 or country code, newer prefixes)

    Args:
        phone_number: Phone number in any of the accepted formats above.

    Returns:
        str: Canonical format '+254' followed by 9-digit national number.

    Raises:
        OTPError: If the phone number format is invalid or doesn't start with
                  a Kenyan mobile prefix (7 or 1).
    """
    # Strip all non-digit characters
    clean_num = ''.join(c for c in phone_number if c.isdigit())

    # Extract 9-digit national significant number based on input length
    if len(clean_num) == 10 and clean_num.startswith('0'):
        # 0712345678 -> 712345678 (drop leading 0)
        national_num = clean_num[1:]
    elif len(clean_num) == 9:
        # 712345678 -> 712345678 (use as-is)
        national_num = clean_num
    elif len(clean_num) == 12 and clean_num.startswith('254'):
        # 254712345678 -> 712345678 (drop country code)
        national_num = clean_num[3:]
    else:
        raise OTPError(
            f'Invalid phone number format. Expected 9, 10, or 12 digits, '
            f'got {len(clean_num)} digits.'
        )

    # Validate Kenyan mobile prefix (7 or 1)
    if national_num[0] not in ('7', '1'):
        raise OTPError(
            f'Invalid phone number prefix. Kenyan mobile numbers must start '
            f'with 7 or 1, got {national_num[0]}.'
        )

    # Return canonical E.164 format
    return f'+254{national_num}'


class OTPError(Exception):
    """OTP verification error."""

    pass


def generate_otp_code():
    """
    Generate a secure 6-digit OTP code.

    Uses secrets.randbelow for cryptographically secure random number generation.

    Returns:
        str: 6-digit OTP code (e.g., '123456')
    """
    code = secrets.randbelow(1000000)
    return f'{code:06d}'


def hash_otp_code(code: str) -> str:
    """Return a keyed HMAC-SHA256 hash for an OTP code."""
    return hmac.new(
        settings.OTP_HASH_KEY.encode(),
        code.encode(),
        hashlib.sha256,
    ).hexdigest()


def create_otp_token(user, phone_number, purpose):
    """
    Create a new OTP token for the user.

    Expires any existing active OTP tokens for this user+purpose combination.
    Then creates a new token with expiry time based on OTP_EXPIRY_MINUTES setting.

    Args:
        user: User instance or None (for registration OTPs)
        phone_number: Phone number for OTP delivery
        purpose: OTP purpose (PHONE_VERIFY, PASSWORD_RESET, LOGIN)

    Returns:
        OTPToken: The created OTP token instance

    Raises:
        OTPError: If OTP token creation fails
    """
    from accounts.models import OTPToken

    # Normalize the phone number before database operations
    formatted_phone = format_phone_number(phone_number)

    try:
        # Expire existing active tokens for this normalized phone+purpose
        query = OTPToken.objects.filter(
            phone_number=formatted_phone,
            purpose=purpose,
            is_used=False,
        )
        if user:
            query = query.filter(user=user)
        query.update(is_used=True)

        # Generate plaintext code for delivery, but persist only its hash.
        plaintext_code = generate_otp_code()
        hashed_code = hash_otp_code(plaintext_code)
        expires_at = timezone.now() + timedelta(
            minutes=settings.OTP_EXPIRY_MINUTES
        )

        # Create new token using the normalized phone number
        token = OTPToken.objects.create(
            user=user,
            phone_number=formatted_phone,
            code=hashed_code,
            purpose=purpose,
            expires_at=expires_at,
            is_used=False,
            attempts=0,
        )
        token.plaintext_code = plaintext_code

        user_email = user.email if user else 'anonymous'
        logger.info(
            f'OTP token created for {user_email} '
            f'(phone={formatted_phone}, purpose={purpose})'
        )
        return token
    except Exception as e:
        error_msg = f'Failed to create OTP token: {str(e)}'
        logger.error(error_msg)
        raise OTPError(error_msg) from e


def verify_otp(phone_number, code, purpose):
    """
    Verify OTP code.

    Finds a valid, unused OTP token matching the phone number, code, and purpose.
    Validates that the token hasn't expired and hasn't exceeded max attempts.
    Increments attempt counter.

    Args:
        phone_number: Phone number associated with OTP
        code: OTP code to verify
        purpose: OTP purpose (PHONE_VERIFY, PASSWORD_RESET, LOGIN)

    Returns:
        OTPToken: The verified OTP token instance

    Raises:
        OTPError: If token not found, expired, or incorrect
    """
    from accounts.models import OTPToken
    formatted_phone = format_phone_number(phone_number)

    # Find token
    token = OTPToken.objects.filter(
        phone_number=formatted_phone,
        purpose=purpose,
        is_used=False,
        expires_at__gt=timezone.now(),
    ).first()

    if not token:
        logger.warning(
            f'OTP token not found for phone={phone_number}, purpose={purpose}'
        )
        raise OTPError('Invalid or expired code.')

    # Increment attempts
    token.attempts += 1
    token.save(update_fields=['attempts'])

    # Check attempt limit
    if token.attempts > settings.OTP_MAX_ATTEMPTS:
        logger.warning(
            f'OTP max attempts exceeded for phone={phone_number}, '
            f'purpose={purpose}'
        )
        raise OTPError('Too many attempts. Please request a new code.')

    submitted_code_hash = hash_otp_code(code)

    # Verify code
    if not hmac.compare_digest(token.code, submitted_code_hash):
        logger.warning(
            f'Incorrect OTP code for phone={phone_number}, purpose={purpose} '
            f'(attempt {token.attempts}/{settings.OTP_MAX_ATTEMPTS})'
        )
        raise OTPError('Incorrect code.')

    # Mark as used
    token.is_used = True
    token.save(update_fields=['is_used'])

    logger.info(
        f'OTP verified successfully for phone={phone_number}, purpose={purpose}'
    )
    return token
