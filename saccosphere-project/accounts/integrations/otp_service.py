"""Africa's Talking SMS service for general-purpose messaging.

This module provides ATSMSClient for sending SMS messages outside of OTP
flows, such as notifications, bulk SMS campaigns, and guarantor
communications.

For OTP-specific send/verify/reset flows, use
accounts.otp_backends.PhoneOTPBackend instead, which is integrated with the
unified OTP delivery backend system.
"""
import logging

import requests
from django.conf import settings

from config.utils import sanitize_pii

try:
    import africastalking
except ImportError:
    africastalking = None

logger = logging.getLogger('saccosphere.sms')


class ATSMSError(Exception):
    """Africa's Talking SMS service error.

    ``retryable`` tells callers whether a retry stands any chance of
    succeeding. It defaults to True (network blips, timeouts) and is
    overridden False on the subclasses below, which represent Africa's
    Talking rejecting the message outright - retrying sends the exact same
    request and gets the exact same rejection.
    """

    retryable = True


class ATSMSInvalidRecipientError(ATSMSError):
    """The recipient phone number was rejected by Africa's Talking."""

    retryable = False


class ATSMSInsufficientBalanceError(ATSMSError):
    """The Africa's Talking account has insufficient balance."""

    retryable = False


class ATSMSRateLimitError(ATSMSError):
    """Africa's Talking is throttling this account."""

    retryable = True


class ATSMSDeliveryUncertainError(ATSMSError):
    """The HTTP request may have reached Africa's Talking before failing.

    ``africastalking``'s SDK (``Service._make_request``) calls
    ``requests.post()`` directly with no internal retry or error
    handling, so a read-phase timeout or dropped connection here means
    the request body - including the message - may already have been
    transmitted and queued by Africa's Talking even though we never saw
    the response. Retrying would risk sending the same SMS twice and
    double-charging the SACCO, so this is deliberately not retryable;
    callers should surface it for a manual delivery-report check instead
    of an automatic resend.
    """

    retryable = False


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

    # Africa's Talking per-recipient status codes
    # (https://developers.africastalking.com/docs/sms/send status codes).
    # The HTTP call to .send() can return 200 while still reporting a
    # per-recipient failure in the response body - these map those codes to
    # specific exceptions so callers can tell a real failure from success.
    _STATUS_INVALID_RECIPIENT = 102
    _STATUS_INSUFFICIENT_BALANCE = 103
    _STATUS_RATE_LIMITED = {404, 429}
    _STATUS_SUCCESS = {100, 101}

    def __init__(self):
        """Initialize Africa's Talking SMS client with API credentials."""
        if settings.DEBUG:
            self.sms = None
            return

        api_key = settings.AT_API_KEY
        username = settings.AT_USERNAME
        environment = settings.AT_ENVIRONMENT

        if africastalking is None:
            raise ATSMSError(
                'Africa\'s Talking SDK is not installed.'
            )

        if not api_key or not username:
            raise ATSMSError(
                'Africa\'s Talking API key and username must be configured'
            )

        sdk_username = 'sandbox' if environment == 'sandbox' else username
        africastalking.initialize(sdk_username, api_key)

        self.sms = africastalking.SMS

    def _normalize_phone(self, phone_number):
        """
        Normalize phone number to E.164 format for Africa's Talking.

        This function is deprecated. Use
        config.utils.normalize_phone_number() instead.

        Args:
            phone_number: Phone number in any format (e.g., +254123456789,
                         0123456789, 254123456789)

        Returns:
            str: Phone number in E.164 format (+254XXXXXXXXX)

        Raises:
            ATSMSError: If phone number is invalid
        """
        from config.utils import (
            InvalidPhoneNumberError,
            normalize_phone_number,
        )

        try:
            return normalize_phone_number(phone_number)
        except InvalidPhoneNumberError as exc:
            raise ATSMSInvalidRecipientError(str(exc)) from exc

    def _classify_response(self, response):
        """
        Raise a specific ``ATSMSError`` if ``response`` reports a delivery
        failure, even though the HTTP call to Africa's Talking succeeded.

        Africa's Talking returns per-recipient status codes inside a 200
        response, so a successful API call is not the same thing as a
        successful send - this must be checked separately.

        Args:
            response: The response object from africastalking.SMS.send().

        Raises:
            ATSMSInvalidRecipientError: Phone number rejected (code 102).
            ATSMSInsufficientBalanceError: Account out of credit (code 103).
            ATSMSRateLimitError: Account is being throttled (404/429).
        """
        try:
            recipients = response.get('SMSMessageData', {}).get(
                'Recipients', [],
            )
        except AttributeError:
            return

        if not recipients:
            return

        status_code = recipients[0].get('statusCode')
        if status_code in self._STATUS_SUCCESS or status_code is None:
            return

        status_text = recipients[0].get('status', 'Unknown error')
        if status_code == self._STATUS_INVALID_RECIPIENT:
            raise ATSMSInvalidRecipientError(
                f'Invalid recipient phone number: {status_text}',
            )
        if status_code == self._STATUS_INSUFFICIENT_BALANCE:
            raise ATSMSInsufficientBalanceError(
                f'Insufficient Africa\'s Talking balance: {status_text}',
            )
        if status_code in self._STATUS_RATE_LIMITED:
            raise ATSMSRateLimitError(
                f'Africa\'s Talking rate limit exceeded: {status_text}',
            )

        raise ATSMSError(
            f'Africa\'s Talking delivery failed (code {status_code}): '
            f'{status_text}',
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
                f'[DEBUG MODE] OTP SMS prepared for {phone_number} '
                f'({purpose})'
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
            )
            self._classify_response(response)
            logger.info(
                f'OTP sent successfully to {normalized_phone} '
                f'(purpose={purpose}, response={response})'
            )
            return True
        except ATSMSError:
            logger.error(
                f'Africa\'s Talking rejected OTP for {normalized_phone} '
                f'(purpose={purpose}).'
            )
            raise
        except Exception as e:
            error_msg = f'Africa\'s Talking SMS error: {str(e)}'
            logger.error(error_msg)
            raise ATSMSError(error_msg) from e

    def send_sms(self, phone_number, message):
        """
        Send a plain SMS message.

        Args:
            phone_number: Phone number to send SMS to.
            message: Message body.

        Returns:
            bool: True if SMS sent successfully.

        Raises:
            ATSMSError: If SMS sending fails.
        """
        if settings.DEBUG:
            logger.info(
                '[DEBUG MODE] SMS for %s: %s',
                phone_number,
                message,
            )
            return True

        try:
            normalized_phone = self._normalize_phone(phone_number)
        except ATSMSError:
            logger.exception('Phone normalization failed.')
            raise

        try:
            response = self.sms.send(
                message=message,
                recipients=[normalized_phone],
            )
            self._classify_response(response)
            logger.info(
                'SMS sent successfully to %s.',
                sanitize_pii(normalized_phone),
            )
            return True
        except ATSMSError:
            logger.error(
                'Africa\'s Talking rejected SMS for %s.',
                sanitize_pii(normalized_phone),
            )
            raise
        except requests.exceptions.ConnectionError as exc:
            # Connection never completed (DNS failure, refused
            # connection, or a connect-phase timeout) - no bytes of the
            # message reached Africa's Talking, so this is safe to
            # retry.
            error_msg = f'Africa\'s Talking SMS connection error: {exc}'
            logger.warning(error_msg)
            raise ATSMSError(error_msg) from exc
        except requests.exceptions.RequestException as exc:
            error_msg = (
                f'Africa\'s Talking SMS outcome uncertain for '
                f'{sanitize_pii(normalized_phone)}: {exc}'
            )
            logger.error(error_msg)
            raise ATSMSDeliveryUncertainError(error_msg) from exc
        except Exception as e:
            error_msg = f'Africa\'s Talking SMS error: {str(e)}'
            logger.error(error_msg)
            raise ATSMSError(error_msg) from e
