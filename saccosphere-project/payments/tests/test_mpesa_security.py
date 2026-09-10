"""M-Pesa callback security: Daraja signature contract + Railway proxy IP.

Item 1 - Daraja does not sign callbacks. Neither the STK Push callback
(``Body.stkCallback``) nor the B2C result callback (``Result``) carries
``Password``/``Timestamp``/``SecurityCredential``/any HMAC; those belong
only to the STK Push *initiate request*. Sources checked:
https://mpesa-nextjs-docs.vercel.app/handling-callback ("The callback does
not contain password, timestamp, or authentication credentials"),
https://gist.github.com/TralahM/e2bf5235da05fa9f1b5d3f482c8e79e4 (real B2C
payload), https://django-daraja.readthedocs.io/en/latest/pages/apis/ .
So a callback missing those fields MUST still be accepted - rejecting it
would drop 100% of genuine Safaricom traffic.

Item 2 - the app runs on Railway (README "Railway Deployment Processes",
config/settings/production.py). Railway's Envoy edge is one trusted hop:
it sets ``X-Envoy-External-Address`` to the real client IP and appends the
observed peer to ``X-Forwarded-For`` without stripping client-supplied
entries, so only the rightmost XFF entry is trustworthy.
"""

import base64

from django.test import RequestFactory, SimpleTestCase, override_settings

from payments.integrations.mpesa.security import (
    is_safaricom_ip,
    verify_mpesa_signature,
)


class _Req:
    """Minimal stand-in for the request object verify_mpesa_signature reads."""

    def __init__(self, body):
        self._mpesa_callback_body = body
        self.data = body


STK_SUCCESS_CALLBACK = {
    'Body': {
        'stkCallback': {
            'MerchantRequestID': '29115-34620561-1',
            'CheckoutRequestID': 'ws_CO_191220191020363925',
            'ResultCode': 0,
            'ResultDesc': 'The service request is processed successfully.',
            'CallbackMetadata': {
                'Item': [
                    {'Name': 'Amount', 'Value': 1.0},
                    {'Name': 'MpesaReceiptNumber', 'Value': 'NLJ7RT61SV'},
                    {'Name': 'TransactionDate', 'Value': 20191219102115},
                    {'Name': 'PhoneNumber', 'Value': 254708374149},
                ],
            },
        },
    },
}

STK_FAILURE_CALLBACK = {
    'Body': {
        'stkCallback': {
            'MerchantRequestID': '29115-34620561-1',
            'CheckoutRequestID': 'ws_CO_191220191020363925',
            'ResultCode': 1032,
            'ResultDesc': 'Request cancelled by user',
        },
    },
}

B2C_RESULT_CALLBACK = {
    'Result': {
        'ResultType': 0,
        'ResultCode': 0,
        'ResultDesc': 'The service request is processed successfully.',
        'OriginatorConversationID': '10571-7910404-1',
        'ConversationID': 'AG_20191219_00005797af5d7d75f652',
        'TransactionID': 'NLJ41HAY6Q',
        'ResultParameters': {
            'ResultParameter': [
                {'Key': 'TransactionAmount', 'Value': 10},
                {'Key': 'TransactionReceipt', 'Value': 'NLJ41HAY6Q'},
            ],
        },
        'ReferenceData': {
            'ReferenceItem': {
                'Key': 'QueueTimeoutURL',
                'Value': 'https://example.com/timeout',
            },
        },
    },
}


class VerifyMpesaSignatureTest(SimpleTestCase):
    """Item 1: unsigned genuine callbacks are accepted; anomalies rejected."""

    def test_stk_success_callback_without_signature_fields_is_accepted(self):
        # Genuine Daraja STK callbacks never carry password/timestamp.
        self.assertTrue(verify_mpesa_signature(_Req(STK_SUCCESS_CALLBACK)))

    def test_stk_failure_callback_without_signature_fields_is_accepted(self):
        self.assertTrue(verify_mpesa_signature(_Req(STK_FAILURE_CALLBACK)))

    def test_b2c_result_callback_without_signature_fields_is_accepted(self):
        # Genuine Daraja B2C result callbacks never carry password/timestamp.
        self.assertTrue(verify_mpesa_signature(_Req(B2C_RESULT_CALLBACK)))

    def test_callback_with_bogus_password_pair_is_rejected(self):
        # A callback that unexpectedly carries a password/timestamp pair
        # which does not match the request-password formula is anomalous.
        body = {
            'password': 'not-a-real-password',
            'timestamp': '20240101120000',
        }
        self.assertFalse(verify_mpesa_signature(_Req(body)))

    @override_settings(MPESA_SHORTCODE='174379', MPESA_PASSKEY='passkey123')
    def test_callback_whose_password_matches_request_formula_is_accepted(self):
        timestamp = '20240101120000'
        raw = f'174379passkey123{timestamp}'
        password = base64.b64encode(raw.encode()).decode()
        body = {'password': password, 'timestamp': timestamp}
        self.assertTrue(verify_mpesa_signature(_Req(body)))


@override_settings(
    DEBUG=False,
    MPESA_ENVIRONMENT='production',
    MPESA_IP_RANGES_PRODUCTION=['196.201.214.0/23', '196.201.212.0/24'],
)
class IsSafaricomIpUnderRailwayProxyTest(SimpleTestCase):
    """Item 2: correct client IP for a Safaricom-origin callback on Railway."""

    SAFARICOM_IP = '196.201.214.10'
    ATTACKER_IP = '41.90.64.9'
    RAILWAY_INTERNAL_IP = '100.64.0.9'

    def setUp(self):
        self.factory = RequestFactory()

    def _request(self, **meta):
        return self.factory.post('/api/v1/payments/mpesa/callback/', **meta)

    def test_genuine_callback_via_envoy_external_address_is_allowed(self):
        request = self._request(
            HTTP_X_ENVOY_EXTERNAL_ADDRESS=self.SAFARICOM_IP,
            HTTP_X_FORWARDED_FOR=f'{self.SAFARICOM_IP}',
            REMOTE_ADDR=self.RAILWAY_INTERNAL_IP,
        )
        self.assertTrue(is_safaricom_ip(request))

    def test_genuine_callback_via_forwarded_for_only_is_allowed(self):
        request = self._request(
            HTTP_X_FORWARDED_FOR=self.SAFARICOM_IP,
            REMOTE_ADDR=self.RAILWAY_INTERNAL_IP,
        )
        self.assertTrue(is_safaricom_ip(request))

    def test_trailing_railway_internal_hop_is_skipped(self):
        request = self._request(
            HTTP_X_FORWARDED_FOR=(
                f'{self.SAFARICOM_IP}, {self.RAILWAY_INTERNAL_IP}'
            ),
            REMOTE_ADDR=self.RAILWAY_INTERNAL_IP,
        )
        self.assertTrue(is_safaricom_ip(request))

    def test_spoofed_leftmost_forwarded_for_is_rejected(self):
        # Attacker prepends a Safaricom IP; Railway's edge appends the real
        # peer, so the rightmost (trusted) entry is the attacker's IP.
        request = self._request(
            HTTP_X_FORWARDED_FOR=f'{self.SAFARICOM_IP}, {self.ATTACKER_IP}',
            REMOTE_ADDR=self.RAILWAY_INTERNAL_IP,
        )
        self.assertFalse(is_safaricom_ip(request))

    def test_spoofed_envoy_external_address_loses_to_real_edge_value(self):
        # X-Envoy-External-Address is set by Railway's edge; a value that
        # actually arrives is authoritative. Here the real client is the
        # attacker, so even a Safaricom-looking XFF prefix cannot help.
        request = self._request(
            HTTP_X_ENVOY_EXTERNAL_ADDRESS=self.ATTACKER_IP,
            HTTP_X_FORWARDED_FOR=f'{self.SAFARICOM_IP}, {self.ATTACKER_IP}',
            REMOTE_ADDR=self.RAILWAY_INTERNAL_IP,
        )
        self.assertFalse(is_safaricom_ip(request))

    def test_no_headers_at_all_is_rejected(self):
        request = self._request()
        # RequestFactory sets REMOTE_ADDR=127.0.0.1, not a Safaricom IP.
        self.assertFalse(is_safaricom_ip(request))
