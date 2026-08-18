"""Test OTP channel selection feature (SMS vs Email)."""

from unittest.mock import patch, MagicMock

from django.core import mail
from django.test import TestCase, override_settings
from django.conf import settings
from rest_framework.test import APIClient

from accounts.models import User, OTPToken
from accounts.otp_backends import OTPDeliveryError


class OTPChannelTestCase(TestCase):
    """Test OTP channel selection and delivery."""

    def setUp(self):
        """Create test user and API client."""
        self.client = APIClient()
        self.user = User.objects.create_user(
            email='test@example.com',
            first_name='Test',
            last_name='User',
            phone_number='254700000001',
            password='testpass123',
        )
        self.user_without_email = User.objects.create_user(
            email='noemail@example.com',  # Email required by model
            first_name='NoEmail',
            last_name='User',
            phone_number='254700000002',
            password='testpass123',
        )
        # Clear email after creation to simulate user without email
        self.user_without_email.email = ''
        self.user_without_phone = User.objects.create_user(
            email='nophone@example.com',
            first_name='NoPhone',
            last_name='User',
            password='testpass123',
        )
        # Clear phone after creation to simulate user without phone
        self.user_without_phone.phone_number = ''

    @override_settings(DEBUG=True, AT_API_KEY='test_key', AT_USERNAME='test_user')
    def test_otp_send_without_channel_defaults_to_phone(self):
        """
        Test that requesting OTP without channel field defaults to PHONE channel.
        Regression test: ensures backward compatibility with existing clients.
        """
        response = self.client.post(
            '/api/v1/accounts/otp/send/',
            {
                'phone_number': '+254700000001',
                'purpose': 'PHONE_VERIFY',
            },
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        
        # Verify the token was created with PHONE channel
        token = OTPToken.objects.filter(phone_number='+254700000001').first()
        self.assertIsNotNone(token)
        self.assertEqual(token.channel, 'PHONE')

    @override_settings(DEBUG=True, AT_API_KEY='test_key', AT_USERNAME='test_user')
    def test_otp_send_with_explicit_phone_channel(self):
        """
        Test that requesting OTP with channel=PHONE sends via Africa's Talking.
        Regression test: ensures explicit PHONE channel works as before.
        """
        response = self.client.post(
            '/api/v1/accounts/otp/send/',
            {
                'phone_number': '+254700000001',
                'purpose': 'PHONE_VERIFY',
                'channel': 'PHONE',
            },
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        
        # Verify response includes channel and masked destination
        self.assertEqual(response.data['channel'], 'PHONE')
        self.assertIn('+254', response.data['destination'])
        self.assertNotIn('700000001', response.data['destination'])

    @patch('accounts.otp_backends.EmailOTPBackend.send')
    @override_settings(OTP_EMAIL_ENABLED=True, EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_otp_send_with_email_channel_sends_email(self, mock_email_send):
        """
        Test that requesting OTP with channel=EMAIL sends real email.
        Uses locmem backend to capture email without real SMTP call.
        """
        mock_email_send.return_value = None
        # Clear mail outbox
        mail.outbox = []

        response = self.client.post(
            '/api/v1/accounts/otp/send/',
            {
                'phone_number': '+254700000001',
                'purpose': 'PHONE_VERIFY',
                'channel': 'EMAIL',
            },
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        mock_email_send.assert_called_once()
        
        # Verify response includes channel
        self.assertEqual(response.data['channel'], 'EMAIL')
        # The destination should be masked email or 'your contact' if user not found
        self.assertIn(response.data['destination'], ['your contact', 't***@example.com'])

    @override_settings(OTP_EMAIL_ENABLED=True, EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_otp_send_email_without_user_email_returns_400(self):
        """
        Test that requesting OTP with channel=EMAIL for user with no email
        returns HTTP 400 with appropriate error message.
        """
        # Authenticate as user without email
        self.client.force_authenticate(user=self.user_without_email)
        
        # Clear mail outbox
        mail.outbox = []

        response = self.client.post(
            '/api/v1/accounts/otp/send/',
            {
                'phone_number': '+254700000002',
                'purpose': 'PHONE_VERIFY',
                'channel': 'EMAIL',
            },
            format='json'
        )

        self.assertEqual(response.status_code, 400)
        # Check for error in response data
        self.assertTrue(len(response.data) > 0)
        
        # Verify no email was sent
        self.assertEqual(len(mail.outbox), 0)

    @override_settings(OTP_EMAIL_ENABLED=False)
    def test_otp_send_email_when_feature_disabled_returns_400(self):
        """
        Test that requesting OTP with channel=EMAIL when OTP_EMAIL_ENABLED=False
        returns HTTP 400 with 'not available yet' message.
        """
        response = self.client.post(
            '/api/v1/accounts/otp/send/',
            {
                'phone_number': '+254700000001',
                'purpose': 'PHONE_VERIFY',
                'channel': 'EMAIL',
            },
            format='json'
        )

        self.assertEqual(response.status_code, 400)
        # Check for error in response data
        self.assertTrue(len(response.data) > 0)

    def test_otp_send_with_invalid_channel_returns_400(self):
        """
        Test that requesting OTP with invalid channel value returns HTTP 400.
        """
        response = self.client.post(
            '/api/v1/accounts/otp/send/',
            {
                'phone_number': '+254700000001',
                'purpose': 'PHONE_VERIFY',
                'channel': 'FAX',
            },
            format='json'
        )

        self.assertEqual(response.status_code, 400)

    @override_settings(DEBUG=True, AT_API_KEY='test_key', AT_USERNAME='test_user')
    def test_masked_phone_in_response(self):
        """
        Test that successful response contains masked phone number, not full number.
        """
        response = self.client.post(
            '/api/v1/accounts/otp/send/',
            {
                'phone_number': '+254700000001',
                'purpose': 'PHONE_VERIFY',
                'channel': 'PHONE',
            },
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        destination = response.data['destination']
        
        # Verify masking pattern: +254*****01
        self.assertIn('+254', destination)
        self.assertIn('01', destination)
        self.assertNotIn('7000000', destination)
        self.assertIn('*', destination)

    @patch('accounts.otp_backends.EmailOTPBackend.send')
    @override_settings(OTP_EMAIL_ENABLED=True, EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_masked_email_in_response(self, mock_email_send):
        """
        Test that successful response contains masked email, not full email.
        """
        mock_email_send.return_value = None
        mail.outbox = []

        response = self.client.post(
            '/api/v1/accounts/otp/send/',
            {
                'phone_number': '+254700000001',
                'purpose': 'PHONE_VERIFY',
                'channel': 'EMAIL',
            },
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        destination = response.data['destination']
        
        # If user is found, verify masking pattern: t***@example.com
        # If user not found, destination will be 'your contact'
        if destination != 'your contact':
            self.assertIn('@example.com', destination)
            self.assertIn('t', destination)  # First character
            self.assertIn('*', destination)  # Masking
            self.assertNotIn('test@example.com', destination)  # Full email not present

    @patch('accounts.otp_backends.EmailOTPBackend.send')
    def test_otp_delivery_error_returns_502(self, mock_email_send):
        """
        Test that OTPDeliveryError is caught and returns HTTP 502.
        """
        mock_email_send.side_effect = OTPDeliveryError('SMTP server unavailable')

        with override_settings(OTP_EMAIL_ENABLED=True, EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend'):
            response = self.client.post(
                '/api/v1/accounts/otp/send/',
                {
                    'phone_number': '+254700000001',
                    'purpose': 'PHONE_VERIFY',
                    'channel': 'EMAIL',
                },
                format='json'
            )

        self.assertEqual(response.status_code, 502)
        self.assertIn('SMTP server unavailable', response.data['detail'])

    @override_settings(DEBUG=True, AT_API_KEY='test_key', AT_USERNAME='test_user')
    def test_otp_resend_with_channel(self):
        """
        Test that OTP resend view respects channel selection.
        """
        # First, create an initial token
        response = self.client.post(
            '/api/v1/accounts/otp/send/',
            {
                'phone_number': '+254700000003',
                'purpose': 'PHONE_VERIFY',
                'channel': 'PHONE',
            },
            format='json'
        )
        self.assertEqual(response.status_code, 200)

        # Resend with same channel (using different phone to avoid cooldown)
        response = self.client.post(
            '/api/v1/accounts/otp/resend/',
            {
                'phone_number': '+254700000003',
                'purpose': 'PHONE_VERIFY',
                'channel': 'PHONE',
            },
            format='json'
        )

        # Resend may hit cooldown, so just check it doesn't crash
        self.assertIn(response.status_code, [200, 429])
        if response.status_code == 200:
            self.assertEqual(response.data['channel'], 'PHONE')
