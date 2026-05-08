"""Africa's Talking SMS service for OTP delivery."""
import logging
import re

import africastalking
from django.conf import settings

logger = logging.getLogger('saccosphere.sms')


class ATSMSError(Exception):
    """Africa's Talking SMS service error."""

    pass


class ATSMSClient:
    """Africa's Talking SMS client for sending OTP codes."""

    # Message templates for different OTP purposes
    OTP_TEMPLATES = {
        'PHONE_VERIFY': (
            'Your SaccoSphere verification code is {code}. '
            'Expires in 5 minutes. Do not share.'
        ),
        'PASSWORD_RESET': (
            'Your SaccoSphere password reset code is {code}. '
            'Expires in 5 minutes.'
        ),
        'LOGIN': (
            'Your SaccoSphere login code is {code}. '
            'Expires in 5 minutes.'
        ),
    }

    def __init__(self):
        """Initialize Africa's Talking SMS client with API credentials."""
        api_key = settings.AT_API_KEY
        username = settings.AT_USERNAME

        if not api_key or not username:
            raise ATSMSError(
                'Africa\'s Talking API key and username must be configured'
            )

        africastalking.initialize(username, api_key)
        self.sms = africastalking.SMS

    def _normalize_phone(self, phone_number):
        """
        Normalize phone number to 254XXXXXXXXX format.

        Args:
            phone_number: Phone number in any format (e.g., +254123456789,
                         0123456789, 254123456789)

        Returns:
            str: Phone number in 254XXXXXXXXX format

        Raises:
            ATSMSError: If phone number is invalid
        """
        # Remove all non-digit characters
        cleaned = re.sub(r'\D', '', phone_number)

        # Handle different input formats
        if cleaned.startswith('254'):
            # Already in 254XXXXXXXXX format
            return cleaned
        elif cleaned.startswith('0') and len(cleaned) == 10:
            # Local format (0XXXXXXXXX) — prepend 254 without 0
            return f'254{cleaned[1:]}'
        else:
            raise ATSMSError(
                f'Invalid phone number format: {phone_number}. '
                'Expected +254XXXXXXXXX or 0XXXXXXXXX format.'
            )

    def send_otp(self, phone_number, code, purpose):
        """
        Send OTP code via SMS.

        Args:
            phone_number: Phone number to send OTP to
            code: 6-digit OTP code
            purpose: OTP purpose (PHONE_VERIFY, PASSWORD_RESET, LOGIN)

        Returns:
            bool: True if SMS sent successfully

        Raises:
            ATSMSError: If SMS sending fails
        """
        # In DEBUG mode, log instead of sending
        if settings.DEBUG:
            logger.info(
                f'[DEBUG MODE] OTP Code for {phone_number} ({purpose}): {code}'
            )
            return True

        # Get message template
        if purpose not in self.OTP_TEMPLATES:
            raise ATSMSError(f'Unknown OTP purpose: {purpose}')

        message = self.OTP_TEMPLATES[purpose].format(code=code)

        # Normalize phone number
        try:
            normalized_phone = self._normalize_phone(phone_number)
        except ATSMSError as e:
            logger.error(f'Phone normalization failed: {str(e)}')
            raise

        # Send SMS via Africa's Talking
        try:
            response = self.sms.send(
                message=message,
                recipients=[normalized_phone],
                sender_id='SaccoSphere',
            )
            logger.info(
                f'OTP sent successfully to {normalized_phone} '
                f'(purpose={purpose}, response={response})'
            )
            return True
        except Exception as e:
            error_msg = f'Africa\'s Talking SMS error: {str(e)}'
            logger.error(error_msg)
            raise ATSMSError(error_msg) from e
