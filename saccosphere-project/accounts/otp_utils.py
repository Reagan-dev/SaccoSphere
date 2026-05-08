"""OTP code generation and verification utilities."""
import logging
import secrets
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger('saccosphere.otp')


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


def create_otp_token(user, phone_number, purpose):
    """
    Create a new OTP token for the user.

    Expires any existing active OTP tokens for this user+purpose combination.
    Then creates a new token with expiry time based on OTP_EXPIRY_MINUTES setting.

    Args:
        user: User instance
        phone_number: Phone number for OTP delivery
        purpose: OTP purpose (PHONE_VERIFY, PASSWORD_RESET, LOGIN)

    Returns:
        OTPToken: The created OTP token instance

    Raises:
        OTPError: If OTP token creation fails
    """
    from accounts.models import OTPToken

    try:
        # Expire existing active tokens for this user+purpose by marking as used
        OTPToken.objects.filter(
            user=user,
            purpose=purpose,
            is_used=False,
        ).update(is_used=True)

        # Generate code and expiry time
        code = generate_otp_code()
        expires_at = timezone.now() + timedelta(
            minutes=settings.OTP_EXPIRY_MINUTES
        )

        # Create new token
        token = OTPToken.objects.create(
            user=user,
            phone_number=phone_number,
            code=code,
            purpose=purpose,
            expires_at=expires_at,
            is_used=False,
            attempts=0,
        )

        logger.info(
            f'OTP token created for user {user.email} '
            f'(phone={phone_number}, purpose={purpose})'
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

    # Find token
    token = OTPToken.objects.filter(
        phone_number=phone_number,
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

    # Verify code
    if token.code != code:
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
