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
    """
    rate = '5/hour'
    
    def get_cache_key(self, request, view):
        """
        Use phone number from request data for cache key.
        """
        phone_number = request.data.get('phone_number', '')
        if phone_number:
            return f'otp_send_{phone_number}'
        return super().get_cache_key(request, view)
    
    def throttle_failure(self):
        """
        Raise Throttled exception with custom message.
        """
        raise Throttled('Too many OTP requests. Please try again later.')
