from unittest.mock import patch

from django.test import TestCase, override_settings

from .integrations.mpesa.daraja import DarajaClient, DarajaError


class FakeResponse:
    def raise_for_status(self):
        return None

    def json(self):
        raise ValueError('Invalid JSON')


class DarajaClientTests(TestCase):
    @override_settings(
        MPESA_CONSUMER_KEY='',
        MPESA_CONSUMER_SECRET='',
        MPESA_SHORTCODE='',
        MPESA_PASSKEY='',
        MPESA_CALLBACK_BASE_URL='',
    )
    def test_stk_push_requires_mpesa_settings(self):
        with self.assertRaisesMessage(
            DarajaError,
            (
                'M-Pesa configuration is missing: MPESA_CONSUMER_KEY, '
                'MPESA_CONSUMER_SECRET, MPESA_SHORTCODE, MPESA_PASSKEY, '
                'MPESA_CALLBACK_BASE_URL'
            ),
        ):
            DarajaClient().initiate_stk_push(
                phone_number='254712345678',
                amount='10.00',
                account_reference='SS-TEST',
                description='Test payment',
                callback_path='/api/v1/payments/callback/mpesa/stk/',
            )

    @patch('payments.integrations.mpesa.daraja.cache')
    @patch('payments.integrations.mpesa.daraja.requests.get')
    @override_settings(
        MPESA_CONSUMER_KEY='test-key',
        MPESA_CONSUMER_SECRET='test-secret',
    )
    def test_get_access_token_rejects_non_json_response(
        self,
        mock_get,
        mock_cache,
    ):
        mock_cache.get.return_value = None
        mock_get.return_value = FakeResponse()

        with self.assertRaisesMessage(
            DarajaError,
            'M-Pesa access token response was not valid JSON.',
        ):
            DarajaClient().get_access_token()

    @patch('payments.integrations.mpesa.daraja.requests.post')
    def test_post_rejects_non_json_response(self, mock_post):
        mock_post.return_value = FakeResponse()

        with self.assertRaisesMessage(
            DarajaError,
            'M-Pesa response was not valid JSON.',
        ):
            DarajaClient()._post(
                'https://example.test/mpesa',
                'test-token',
                {'Amount': 10},
            )
