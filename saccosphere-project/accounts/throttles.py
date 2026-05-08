"""Custom throttling classes for OTP endpoints."""

from rest_framework.throttling import AnonRateThrottle
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
    
    def throttle_failure(self, request, response):
        """
        Return custom 429 response with remaining time.
        """
        # Get the most recent OTP token for this phone
        from accounts.models import OTPToken
        
        phone_number = request.data.get('phone_number', '')
        if phone_number:
            try:
                recent_token = OTPToken.objects.filter(
                    phone_number=phone_number
                ).order_by('-created_at').first()
                
                if recent_token:
                    # Calculate time until next allowed send
                    time_passed = timezone.now() - recent_token.created_at
                    cooldown_period = timedelta(seconds=720)  # 12 minutes = 5/hour
                    remaining_time = cooldown_period - time_passed
                    
                    if remaining_time.total_seconds() > 0:
                        minutes_remaining = int(remaining_time.total_seconds() / 60)
                        response.data = {
                            'error': f'Too many OTP requests. Try again in {minutes_remaining} minutes.',
                            'seconds_remaining': int(remaining_time.total_seconds())
                        }
                        response.status_code = 429
                        return response
            except:
                pass
        
        # Fallback response
        response.data = {
            'error': 'Too many OTP requests. Try again later.',
            'seconds_remaining': 720  # 12 minutes
        }
        response.status_code = 429
        return response
