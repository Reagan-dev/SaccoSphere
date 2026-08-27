"""Custom throttling classes for OTP endpoints."""

import logging
from django.conf import settings
from rest_framework.throttling import AnonRateThrottle, UserRateThrottle
from rest_framework.exceptions import Throttled
from django.core.cache import cache
from django.utils import timezone
from datetime import timedelta


logger = logging.getLogger(__name__)


def _get_client_ip(request):
    """
    Get the client IP address from the request.

    Checks for X-Forwarded-For header first (for reverse proxies),
    then falls back to REMOTE_ADDR.
    """
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        # X-Forwarded-For can contain multiple IPs, take the first one
        ip = x_forwarded_for.split(',')[0].strip()
    else:
        ip = request.META.get('REMOTE_ADDR', 'unknown')
    return ip


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


class OTPSendIPThrottle(AnonRateThrottle):
    """
    Throttle OTP sends by IP address to prevent bulk spam attacks.

    Limits the total number of OTP sends from a single IP address,
    regardless of the phone numbers used. This prevents attackers
    from cycling through many phone numbers to bypass per-phone limits.

    Uses a configurable rate from Django settings.
    Default: 20 per hour.
    """
    def __init__(self):
        rate = getattr(settings, 'OTP_SEND_IP_RATE', '20/hour')
        self.rate = rate if rate else '20/hour'
        super().__init__()

    def get_cache_key(self, request, view):
        """
        Use client IP address for cache key.
        """
        ip = _get_client_ip(request)
        return f'otp_send_ip_{ip}'

    def throttle_failure(self):
        """
        Raise Throttled exception with same message as phone throttle
        to avoid leaking strategy to attackers.
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


class OTPVerifyThrottle(AnonRateThrottle):
    """
    Throttle OTP verify requests to 10 per minute per IP address.

    Bounds request volume across all tokens/phones from one IP.
    Independent of per-token OTP_MAX_ATTEMPTS limit (3 wrong guesses per token).
    Uses IP-based throttling since this endpoint is anonymous (AllowAny).
    """
    rate = '10/minute'


class KYCUploadUserThrottle(UserRateThrottle):
    """
    Throttle KYC document uploads for authenticated users.

    Limits uploads per user to prevent storage exhaustion while allowing
    legitimate retries for document errors. Uses a configurable rate from
    Django settings. Default: 10 per hour.

    Justification: A legitimate user typically uploads 2 documents (front/back)
    once, with a few retries for errors. 10/hour allows for legitimate retries
    while preventing storage exhaustion abuse.
    """

    def __init__(self):
        rate = getattr(settings, 'KYC_UPLOAD_USER_RATE', '10/hour')
        self.rate = rate if rate else '10/hour'
        super().__init__()

    def get_cache_key(self, request, view):
        """
        Use user ID for cache key.
        """
        if request.user and request.user.is_authenticated:
            return f'kyc_upload_user_{request.user.id}'
        return None

    def allow_request(self, request, view):
        """
        Store request reference before checking throttle.
        """
        self.request = request
        return super().allow_request(request, view)

    def throttle_failure(self):
        """
        Raise Throttled exception with Retry-After header and log the event.
        """
        wait = self.wait()
        logger.warning(
            'KYC upload throttled for user %s. Wait: %s seconds',
            getattr(self.request.user, 'id', 'anonymous'),
            wait,
            extra={
                'user_id': getattr(self.request.user, 'id', None),
                'throttle_type': 'user',
                'wait_seconds': wait,
            },
        )
        raise Throttled(
            wait=wait,
            detail='Too many KYC upload requests. Please try again later.',
        )


class KYCUploadIPThrottle(AnonRateThrottle):
    """
    Throttle KYC document uploads by IP address.

    Defense-in-depth measure to prevent bulk attacks even if authentication
    changes. Limits total uploads from a single IP regardless of user.
    Uses a configurable rate from Django settings. Default: 20 per hour.

    Matches OTP_SEND_IP_RATE for consistency across sensitive endpoints.
    """

    def __init__(self):
        rate = getattr(settings, 'KYC_UPLOAD_IP_RATE', '20/hour')
        self.rate = rate if rate else '20/hour'
        super().__init__()

    def get_cache_key(self, request, view):
        """
        Use client IP address for cache key.
        """
        ip = _get_client_ip(request)
        return f'kyc_upload_ip_{ip}'

    def allow_request(self, request, view):
        """
        Store request reference before checking throttle.
        """
        self.request = request
        return super().allow_request(request, view)

    def throttle_failure(self):
        """
        Raise Throttled exception with Retry-After header and log the event.
        """
        wait = self.wait()
        ip = _get_client_ip(self.request)
        logger.warning(
            'KYC upload throttled for IP %s. Wait: %s seconds',
            ip,
            wait,
            extra={
                'client_ip': ip,
                'throttle_type': 'ip',
                'wait_seconds': wait,
            },
        )
        raise Throttled(
            wait=wait,
            detail='Too many KYC upload requests. Please try again later.',
        )
