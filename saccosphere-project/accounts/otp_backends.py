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
import time

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string

from .models import OTPToken

logger = logging.getLogger(__name__)
sms_logger = logging.getLogger('accounts.sms')

# In-process counters for SMS delivery metrics
_sms_delivery_success_count = 0
_sms_delivery_failure_count = 0


def _mask_phone(phone_number: str) -> str:
    """
    Mask phone number for logging/monitoring.

    Keeps country code and last 2 digits, masks the middle.
    Example: +254712345678 -> +254*****78

    Args:
        phone_number: Phone number in E.164 format (+254XXXXXXXXX)

    Returns:
        str: Masked phone number
    """
    if len(phone_number) < 4:
        return phone_number
    if phone_number.startswith('+'):
        country_code = phone_number[:4]  # +254
        last_digits = phone_number[-2:]
        middle = '*' * (len(phone_number) - len(country_code) - len(last_digits))
        return f'{country_code}{middle}{last_digits}'
    else:
        # Fallback for non-standard format
        last_digits = phone_number[-2:]
        middle = '*' * (len(phone_number) - len(last_digits))
        return f'{middle}{last_digits}'


class OTPDeliveryError(Exception):
    """Raised when an OTP backend fails to deliver the code."""

    pass


class AfricasTalkingError(OTPDeliveryError):
    """Base exception for Africa's Talking-specific failures."""

    pass


class InsufficientBalanceError(AfricasTalkingError):
    """Raised when Africa's Talking account has insufficient balance."""

    pass


class InvalidRecipientError(AfricasTalkingError):
    """Raised when the phone number is invalid or not supported."""

    pass


class RateLimitError(AfricasTalkingError):
    """Raised when Africa's Talking rate limit is exceeded."""

    pass


class BaseOTPBackend(abc.ABC):
    """
    Abstract base class for OTP delivery backends.

    Subclasses must implement the send() method to deliver OTP codes
    via their specific channel (SMS, email, etc.). The send() method
    must raise OTPDeliveryError on any failure and return None on success.
    """

    channel: str

    def get_plaintext_code(self, token: OTPToken) -> str:
        """Return the non-persisted plaintext code for delivery."""
        code = getattr(token, 'plaintext_code', None)
        if not code:
            raise OTPDeliveryError('OTP plaintext code is not available.')
        return code

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

        sdk_username = 'sandbox' if environment == 'sandbox' else username
        self.africastalking.initialize(sdk_username, api_key)

        self.sms = self.africastalking.SMS

    def _normalize_phone(self, phone_number):
        """
        Normalize phone number to E.164 format for Africa's Talking.

        This function is deprecated. Use config.utils.normalize_phone_number() instead.

        Args:
            phone_number: Phone number in any format (e.g., +254123456789,
                         0123456789, 254123456789)

        Returns:
            str: Phone number in E.164 format (+254XXXXXXXXX)

        Raises:
            OTPDeliveryError: If phone number is invalid
        """
        from config.utils import InvalidPhoneNumberError, normalize_phone_number

        try:
            return normalize_phone_number(phone_number)
        except InvalidPhoneNumberError as exc:
            raise OTPDeliveryError(str(exc)) from exc

    def _classify_error(self, response):
        """
        Classify Africa's Talking response into specific error types.

        Based on africastalking v2.0.2 response format:
        - statusCode 102: Invalid Phone Number -> InvalidRecipientError
        - statusCode 103: Insufficient Balance -> InsufficientBalanceError
        - statusCode 404/429: Rate limit exceeded -> RateLimitError
        - Other errors fall through to generic OTPDeliveryError

        Args:
            response: The response object from africastalking.SMS.send()

        Raises:
            InvalidRecipientError: If phone number is invalid
            InsufficientBalanceError: If account has insufficient balance
            RateLimitError: If rate limit is exceeded
        """
        # africastalking v2.0.2 returns a dict-like object with SMSMessageData
        # This is based on the typical response structure; actual format should
        # be verified with a live sandbox call if behavior differs
        try:
            if hasattr(response, 'get'):
                recipients = response.get('SMSMessageData', {}).get('Recipients', [])
                if recipients:
                    status_code = recipients[0].get('statusCode')
                    if status_code == 102:
                        raise InvalidRecipientError('Invalid phone number')
                    elif status_code == 103:
                        raise InsufficientBalanceError('Insufficient SMS balance')
                    elif status_code in (404, 429):
                        raise RateLimitError('Rate limit exceeded')
        except (AttributeError, KeyError, IndexError):
            # Response structure unexpected, fall through to generic error
            pass

    def send(self, token: OTPToken) -> None:
        """
        Send OTP code via SMS using Africa's Talking.

        Args:
            token: The OTPToken instance containing the code and delivery details.

        Raises:
            OTPDeliveryError: If SMS sending fails.
            AfricasTalkingError: Subclass for specific Africa's Talking failures.

        Returns:
            None
        """
        global _sms_delivery_success_count, _sms_delivery_failure_count

        # In DEBUG mode, log instead of sending
        if settings.DEBUG:
            logger.info(
                f'[DEBUG MODE] OTP SMS prepared for {_mask_phone(token.phone_number)} '
                f'({token.purpose})'
            )
            return

        # Get message template
        if token.purpose not in self.OTP_TEMPLATES:
            raise OTPDeliveryError(f'Unknown OTP purpose: {token.purpose}')

        plaintext_code = self.get_plaintext_code(token)
        message = self.OTP_TEMPLATES[token.purpose].format(
            code=plaintext_code,
        )

        # Normalize phone number
        try:
            normalized_phone = self._normalize_phone(token.phone_number)
        except OTPDeliveryError as e:
            logger.error(f'Phone normalization failed: {str(e)}')
            raise

        masked_phone = _mask_phone(normalized_phone)

        # Send SMS via Africa's Talking with retry for network failures
        last_exception = None
        for attempt in range(2):  # Initial attempt + 1 retry
            try:
                response = self.sms.send(
                    message=message,
                    recipients=[normalized_phone]
                )
                # Classify response into specific error types
                self._classify_error(response)

                _sms_delivery_success_count += 1
                logger.info(
                    f'OTP sent successfully to {masked_phone} '
                    f'(purpose={token.purpose}, channel={self.channel})'
                )
                return

            except (InvalidRecipientError, InsufficientBalanceError, RateLimitError) as e:
                # These errors should not be retried
                _sms_delivery_failure_count += 1
                error_type = type(e).__name__

                # Log to dedicated SMS logger with structured data
                sms_logger.error(
                    f'SMS delivery failed: {error_type} | '
                    f'purpose={token.purpose} | channel={self.channel} | '
                    f'phone={masked_phone}'
                )

                # Send to Sentry if configured
                try:
                    import sentry_sdk
                    sentry_sdk.set_context('otp_delivery', {
                        'purpose': token.purpose,
                        'channel': self.channel,
                        'error_type': error_type,
                    })
                    sentry_sdk.capture_exception(e)
                except ImportError:
                    pass

                raise OTPDeliveryError('Failed to send SMS. Please try again.') from e

            except OTPDeliveryError as e:
                # Other OTP delivery errors
                _sms_delivery_failure_count += 1
                error_type = type(e).__name__

                sms_logger.error(
                    f'SMS delivery failed: {error_type} | '
                    f'purpose={token.purpose} | channel={self.channel} | '
                    f'phone={masked_phone}'
                )

                try:
                    import sentry_sdk
                    sentry_sdk.set_context('otp_delivery', {
                        'purpose': token.purpose,
                        'channel': self.channel,
                        'error_type': error_type,
                    })
                    sentry_sdk.capture_exception(e)
                except ImportError:
                    pass

                raise

            except Exception as e:
                last_exception = e
                if attempt == 0:
                    # First attempt failed, retry after delay for network issues
                    logger.warning(
                        f'First SMS send attempt failed, retrying in 1s: {str(e)}'
                    )
                    time.sleep(1)
                else:
                    # Retry also failed
                    _sms_delivery_failure_count += 1
                    sms_logger.error(
                        f'SMS delivery failed after retry: network_error | '
                        f'purpose={token.purpose} | channel={self.channel} | '
                        f'phone={masked_phone}'
                    )

                    try:
                        import sentry_sdk
                        sentry_sdk.set_context('otp_delivery', {
                            'purpose': token.purpose,
                            'channel': self.channel,
                            'error_type': 'network_error',
                        })
                        sentry_sdk.capture_exception(e)
                    except ImportError:
                        pass

                    raise OTPDeliveryError(
                        'Failed to send SMS. Please try again.'
                    ) from last_exception


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
            'otp_code': self.get_plaintext_code(token),
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
