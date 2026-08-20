"""OTP delivery backends for SMS and email channels.

This module provides unified OTP delivery backends for OTP-specific flows:
- PhoneOTPBackend: Sends OTP codes via Africa's Talking SMS for OTP flows
- EmailOTPBackend: Sends OTP codes via email for OTP flows

These backends are used by OTPSendView, OTPResendView, and PasswordResetRequestView
for OTP-specific send/verify/reset functionality.

For general-purpose SMS messaging (notifications, bulk SMS, guarantor communications),
use accounts.integrations.otp_service.ATSMSClient instead.
"""

import abc
import logging
import smtplib

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string

from .models import OTPToken

logger = logging.getLogger(__name__)


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
        environment = settings.AT_ENVIRONMENT

        if self.africastalking is None:
            raise OTPDeliveryError(
                'Africa\'s Talking SDK is not installed.'
            )

        if not api_key or not username:
            raise OTPDeliveryError(
                'Africa\'s Talking API key and username must be configured'
            )

        self.africastalking.initialize(username, api_key)

        # Enable sandbox mode if configured
        if environment == 'sandbox':
            self.africastalking.set_sandbox(True)

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
    """Email backend for OTP delivery using Django's mail system."""

    channel = OTPToken.Channel.EMAIL

    def send(self, token: OTPToken) -> None:
        """
        Send OTP code via email using Django's EmailMultiAlternatives.

        Renders both text and HTML email templates and sends them to the
        user's email address. Validates that the user has an email before
        attempting to send.

        Args:
            token: The OTPToken instance containing the code and delivery details.

        Raises:
            OTPDeliveryError: If the user has no email or sending fails.

        Returns:
            None
        """
        user = token.user
        if user is None:
            raise OTPDeliveryError('This account has no email address on file.')

        if not user.email:
            raise OTPDeliveryError('This account has no email address on file.')

        expiry_minutes = getattr(settings, 'OTP_EXPIRY_MINUTES', 10)

        context = {
            'otp_code': token.code,
            'expiry_minutes': expiry_minutes,
            'user': user,
        }

        subject = 'Your SaccoSphere verification code'
        text_body = render_to_string('accounts/emails/otp_email.txt', context)
        html_body = render_to_string('accounts/emails/otp_email.html', context)

        from_email = settings.DEFAULT_FROM_EMAIL

        try:
            email = EmailMultiAlternatives(
                subject=subject,
                body=text_body,
                from_email=from_email,
                to=[user.email],
            )
            email.attach_alternative(html_body, 'text/html')
            email.send()
            logger.info(
                f'OTP email sent successfully to user {user.id} '
                f'(purpose={token.purpose})'
            )
        except smtplib.SMTPException as e:
            logger.error(
                f'Failed to send OTP email to user {user.id}: {str(e)}'
            )
            raise OTPDeliveryError(
                'Could not send verification email. Please try again.'
            ) from e
        except Exception as e:
            logger.error(
                f'Failed to send OTP email to user {user.id}: {str(e)}'
            )
            raise OTPDeliveryError(
                'Could not send verification email. Please try again.'
            ) from e


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
