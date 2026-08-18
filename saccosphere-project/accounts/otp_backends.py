"""OTP delivery backends for SMS and email channels."""

import abc
import logging

from django.conf import settings

from .models import OTPToken

logger = logging.getLogger('saccosphere.otp')


class OTPDeliveryError(Exception):
    """Raised when an OTP backend fails to deliver the code."""

    pass


class BaseOTPBackend(abc.ABC):
    """
    Abstract base class for OTP delivery backends.

    Subclasses must implement the send() method to deliver OTP codes
    via their specific channel (SMS, email, etc.). The send() method
    must raise OTPDeliveryError on any failure and return None on success.
    """

    channel: str

    @abc.abstractmethod
    def send(self, token: OTPToken) -> None:
        """
        Send the OTP code via this backend's delivery channel.

        Args:
            token: The OTPToken instance containing the code and delivery details.

        Raises:
            OTPDeliveryError: If the OTP fails to send.

        Returns:
            None
        """
        pass


class PhoneOTPBackend(BaseOTPBackend):
    """Africa's Talking SMS backend for OTP delivery."""

    channel = OTPToken.Channel.PHONE

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
        try:
            import africastalking
        except ImportError:
            self.africastalking = None
        else:
            self.africastalking = africastalking

        if settings.DEBUG:
            self.sms = None
            return

        api_key = settings.AT_API_KEY
        username = settings.AT_USERNAME

        if self.africastalking is None:
            raise OTPDeliveryError(
                'Africa\'s Talking SDK is not installed.'
            )

        if not api_key or not username:
            raise OTPDeliveryError(
                'Africa\'s Talking API key and username must be configured'
            )

        self.africastalking.initialize(username, api_key)
        self.sms = self.africastalking.SMS

    def _normalize_phone(self, phone_number):
        """
        Normalize phone number to 254XXXXXXXXX format.

        Args:
            phone_number: Phone number in any format (e.g., +254123456789,
                         0123456789, 254123456789)

        Returns:
            str: Phone number in 254XXXXXXXXX format

        Raises:
            OTPDeliveryError: If phone number is invalid
        """
        # Remove all non-digit characters
        clean_num = ''.join(c for c in phone_number if c.isdigit())

        # If the user input was 254... (12 digits) or 07... (10 digits)
        if len(clean_num) == 12 and clean_num.startswith('254'):
            return f'+{clean_num}'
        elif len(clean_num) == 9 and clean_num.startswith('7'):  # 07... stripped
            return f'+254{clean_num}'

        # Fallback: if it's already 13 digits starting with 254 but no plus
        return f'+{clean_num}'

    def send(self, token: OTPToken) -> None:
        """
        Send OTP code via SMS using Africa's Talking.

        Args:
            token: The OTPToken instance containing the code and delivery details.

        Raises:
            OTPDeliveryError: If SMS sending fails.

        Returns:
            None
        """
        # In DEBUG mode, log instead of sending
        if settings.DEBUG:
            logger.info(
                f'[DEBUG MODE] OTP Code for {token.phone_number} '
                f'({token.purpose}): {token.code}'
            )
            return

        # Get message template
        if token.purpose not in self.OTP_TEMPLATES:
            raise OTPDeliveryError(f'Unknown OTP purpose: {token.purpose}')

        message = self.OTP_TEMPLATES[token.purpose].format(code=token.code)

        # Normalize phone number
        try:
            normalized_phone = self._normalize_phone(token.phone_number)
        except OTPDeliveryError as e:
            logger.error(f'Phone normalization failed: {str(e)}')
            raise

        # Send SMS via Africa's Talking
        try:
            response = self.sms.send(
                message=message,
                recipients=[normalized_phone]
            )
            logger.info(
                f'OTP sent successfully to {normalized_phone} '
                f'(purpose={token.purpose}, response={response})'
            )
        except Exception as e:
            error_msg = f'Africa\'s Talking SMS error: {str(e)}'
            logger.error(error_msg)
            raise OTPDeliveryError(error_msg) from e


class EmailOTPBackend(BaseOTPBackend):
    """
    Email backend for OTP delivery.

    This is a placeholder implementation. The real email sending logic
    will be implemented in a subsequent step.
    """

    channel = OTPToken.Channel.EMAIL

    def send(self, token: OTPToken) -> None:
        """
        Send OTP code via email.

        Args:
            token: The OTPToken instance containing the code and delivery details.

        Raises:
            NotImplementedError: This backend is not yet implemented.

        Returns:
            None
        """
        raise NotImplementedError('Email OTP backend not implemented yet')


def get_otp_backend(channel: str) -> BaseOTPBackend:
    """
    Factory function to get the appropriate OTP backend for a channel.

    Args:
        channel: The delivery channel ('PHONE' or 'EMAIL').

    Returns:
        BaseOTPBackend: An instance of the appropriate backend.

    Raises:
        ValueError: If the channel is not supported.

    Example:
        >>> backend = get_otp_backend('PHONE')
        >>> backend.send(token)
    """
    if channel == OTPToken.Channel.PHONE:
        return PhoneOTPBackend()
    elif channel == OTPToken.Channel.EMAIL:
        return EmailOTPBackend()
    else:
        raise ValueError(f'Unsupported OTP channel: {channel}')
