"""Tests for ATSMSClient's Africa's Talking response classification.

Africa's Talking can return HTTP 200 while still reporting, inside the
response body, that a specific recipient was rejected (bad number, no
balance, rate limited). ``ATSMSClient.send_sms``/``send_otp`` must turn
that per-recipient status into a raised, specifically-typed error instead
of reporting success - these tests pin that behaviour down.
"""
from unittest.mock import Mock

import requests
from django.test import TestCase, override_settings

from accounts.integrations.otp_service import (
    ATSMSClient,
    ATSMSDeliveryUncertainError,
    ATSMSError,
    ATSMSInsufficientBalanceError,
    ATSMSInvalidRecipientError,
    ATSMSRateLimitError,
)


def _at_response(status_code, status='Success'):
    return {
        'SMSMessageData': {
            'Message': status,
            'Recipients': [
                {
                    'statusCode': status_code,
                    'status': status,
                    'number': '+254712345678',
                },
            ],
        },
    }


def _build_client(send_return_value=None, send_side_effect=None):
    """Build an ATSMSClient with a stubbed Africa's Talking SDK handle,
    bypassing __init__ (which requires real AT credentials/settings)."""
    client = ATSMSClient.__new__(ATSMSClient)
    client.sms = Mock()
    if send_side_effect is not None:
        client.sms.send.side_effect = send_side_effect
    else:
        client.sms.send.return_value = send_return_value
    return client


@override_settings(DEBUG=False)
class ATSMSClientClassificationTests(TestCase):
    def test_send_sms_returns_true_on_success_status_code(self):
        client = _build_client(_at_response(101))

        result = client.send_sms('+254712345678', 'hello')

        self.assertTrue(result)

    def test_send_sms_raises_invalid_recipient_on_status_102(self):
        client = _build_client(
            _at_response(102, status='InvalidPhoneNumber'),
        )

        with self.assertRaises(ATSMSInvalidRecipientError):
            client.send_sms('+254712345678', 'hello')

    def test_send_sms_raises_insufficient_balance_on_status_103(self):
        client = _build_client(
            _at_response(103, status='InsufficientBalance'),
        )

        with self.assertRaises(ATSMSInsufficientBalanceError):
            client.send_sms('+254712345678', 'hello')

    def test_send_sms_raises_rate_limit_on_status_429(self):
        client = _build_client(_at_response(429, status='ThrottlingError'))

        with self.assertRaises(ATSMSRateLimitError):
            client.send_sms('+254712345678', 'hello')

    def test_send_sms_raises_rate_limit_on_status_404(self):
        client = _build_client(_at_response(404, status='UnknownError'))

        with self.assertRaises(ATSMSRateLimitError):
            client.send_sms('+254712345678', 'hello')

    def test_send_sms_raises_generic_error_on_unmapped_status_code(self):
        client = _build_client(_at_response(500, status='GatewayError'))

        with self.assertRaises(ATSMSError):
            client.send_sms('+254712345678', 'hello')

    def test_send_sms_tolerates_missing_recipients_key(self):
        """An unexpected response shape should not itself crash delivery -
        it falls through to reporting success, matching the same
        defensive fallback accounts.otp_backends already relies on."""
        client = _build_client({'SMSMessageData': {}})

        result = client.send_sms('+254712345678', 'hello')

        self.assertTrue(result)

    def test_send_otp_raises_insufficient_balance_on_status_103(self):
        client = _build_client(_at_response(103))

        with self.assertRaises(ATSMSInsufficientBalanceError):
            client.send_otp('+254712345678', '123456', 'LOGIN')

    def test_sdk_exception_still_wrapped_as_generic_atsms_error(self):
        client = _build_client(send_side_effect=RuntimeError('network down'))

        with self.assertRaises(ATSMSError) as ctx:
            client.send_sms('+254712345678', 'hello')

        self.assertNotIsInstance(
            ctx.exception,
            (
                ATSMSInvalidRecipientError,
                ATSMSInsufficientBalanceError,
                ATSMSRateLimitError,
            ),
        )

    def test_connection_error_is_retryable_no_message_was_sent(self):
        """africastalking.Service._make_request calls requests.post()
        directly with no internal error handling, so a connection-phase
        failure means our request never reached Africa's Talking - safe
        to retry."""
        client = _build_client(
            send_side_effect=requests.exceptions.ConnectionError('refused'),
        )

        with self.assertRaises(ATSMSError) as ctx:
            client.send_sms('+254712345678', 'hello')

        self.assertNotIsInstance(ctx.exception, ATSMSDeliveryUncertainError)
        self.assertTrue(ctx.exception.retryable)

    def test_connect_timeout_is_retryable_no_message_was_sent(self):
        """ConnectTimeout is a ConnectionError subclass (connect-phase),
        distinct from ReadTimeout (request already sent) below."""
        client = _build_client(
            send_side_effect=requests.exceptions.ConnectTimeout('timed out'),
        )

        with self.assertRaises(ATSMSError) as ctx:
            client.send_sms('+254712345678', 'hello')

        self.assertNotIsInstance(ctx.exception, ATSMSDeliveryUncertainError)
        self.assertTrue(ctx.exception.retryable)

    def test_read_timeout_is_not_retryable_message_may_have_sent(self):
        """A read-phase timeout means our POST (message included) was
        already transmitted before Africa's Talking's response was lost
        - retrying risks a duplicate, chargeable SMS, so this must not
        be auto-retried."""
        client = _build_client(
            send_side_effect=requests.exceptions.ReadTimeout('no response'),
        )

        with self.assertRaises(ATSMSDeliveryUncertainError) as ctx:
            client.send_sms('+254712345678', 'hello')

        self.assertFalse(ctx.exception.retryable)


class ATSMSErrorRetryabilityTests(TestCase):
    def test_base_error_is_retryable_by_default(self):
        self.assertTrue(ATSMSError('down').retryable)

    def test_invalid_recipient_is_not_retryable(self):
        self.assertFalse(ATSMSInvalidRecipientError('bad number').retryable)

    def test_insufficient_balance_is_not_retryable(self):
        self.assertFalse(
            ATSMSInsufficientBalanceError('no balance').retryable,
        )

    def test_rate_limit_is_retryable(self):
        self.assertTrue(ATSMSRateLimitError('throttled').retryable)
