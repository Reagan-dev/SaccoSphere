"""Custom throttling classes for OTP endpoints."""

from rest_framework.throttling import AnonRateThrottle
from rest_framework.exceptions import Throttled
from django.core.cache import cache
from django.utils import timezone
from datetime import timedelta


class OTPSendThrottle(AnonRateThrottle):
    """
    Throttle OTP sends to 5 per phone number per hour.

    Uses phone number from request data for cache key instead of IP,
    so multiple users on same IP can each send OTPs.
    Includes user ID in cache key if authenticated to prevent
    channel switching from bypassing rate limits.
    """
    rate = '5/hour'

    def get_cache_key(self, request, view):
        """
        Use phone number from request data for cache key.
        Include user ID if authenticated to prevent channel switching bypass.
        """
        phone_number = request.data.get('phone_number', '')
        channel = request.data.get('channel', 'PHONE')

        if request.user and request.user.is_authenticated:
            user_id = request.user.id
            return f'otp_send_{phone_number}_{channel}_{user_id}'
        elif phone_number:
            return f'otp_send_{phone_number}_{channel}'
        return super().get_cache_key(request, view)

    def throttle_failure(self):
        """
        Raise Throttled exception with custom message.
        """
        raise Throttled(
            wait=None,
            detail='Too many OTP requests. Please try again later.'
        )


class GoogleOAuthThrottle(AnonRateThrottle):
    """
    Throttle Google OAuth callback to 10 requests per minute per IP address.

    Allows legitimate retries on flaky connections while preventing abuse.
    Uses IP-based throttling since mobile apps typically have stable IPs
    and this endpoint is anonymous (no user authentication yet).
    """
    rate = '10/minute'
