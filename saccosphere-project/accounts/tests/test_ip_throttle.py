"""Tests for IP-based OTP send throttling."""

from django.test import TestCase, override_settings
from django.core.cache import cache
from rest_framework.test import APIClient
from rest_framework import status
from unittest.mock import patch

from accounts.throttles import OTPSendIPThrottle


class OTPSendIPThrottleTestCase(TestCase):
    """Test IP-based throttling for OTP send requests."""

    def setUp(self):
        """Set up test client and clear cache."""
        self.client = APIClient()
        cache.clear()

    @patch('accounts.views.get_otp_backend')
    @override_settings(
        OTP_SEND_IP_RATE='3/hour',
        DEBUG=True,
        AT_API_KEY='test_key',
        AT_USERNAME='test_user'
    )
    def test_requests_under_ip_limit_succeed_across_phones(self, mock_backend):
        """
        Test that requests under the IP limit succeed even across
        many different phone numbers.
        """
        mock_backend.return_value.send.return_value = None

        # Make 3 requests with different phone numbers (limit is 3/hour)
        for i in range(3):
            response = self.client.post(
                '/api/v1/accounts/otp/send/',
                {
                    'phone_number': f'+25470000000{i}',
                    'channel': 'PHONE',
                    'purpose': 'PHONE_VERIFY',
                },
                format='json',
            )
            self.assertEqual(response.status_code, status.HTTP_200_OK)

    @patch('accounts.views.get_otp_backend')
    @override_settings(
        OTP_SEND_IP_RATE='3/hour',
        DEBUG=True,
        AT_API_KEY='test_key',
        AT_USERNAME='test_user'
    )
    def test_requests_exceeding_ip_limit_are_blocked(self, mock_backend):
        """
        Test that requests exceeding the IP limit are blocked.
        """
        mock_backend.return_value.send.return_value = None

        # Make 3 requests (limit is 3/hour)
        for i in range(3):
            response = self.client.post(
                '/api/v1/accounts/otp/send/',
                {
                    'phone_number': f'+25470000000{i}',
                    'channel': 'PHONE',
                    'purpose': 'PHONE_VERIFY',
                },
                format='json',
            )
            self.assertEqual(response.status_code, status.HTTP_200_OK)

        # 4th request should be throttled
        response = self.client.post(
            '/api/v1/accounts/otp/send/',
            {
                'phone_number': '+254700000009',
                'channel': 'PHONE',
                'purpose': 'PHONE_VERIFY',
            },
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
        # DRF Throttled exception returns detail in response.data
        self.assertIn('Too many OTP requests', str(response.data))

    @patch('accounts.views.get_otp_backend')
    @override_settings(
        OTP_SEND_IP_RATE='3/hour',
        DEBUG=True,
        AT_API_KEY='test_key',
        AT_USERNAME='test_user'
    )
    def test_per_phone_throttle_still_independently_applies(self, mock_backend):
        """
        Test that the per-phone throttle still independently applies
        alongside the IP throttle.
        """
        mock_backend.return_value.send.return_value = None

        # Make 5 requests to the SAME phone number
        # Per-phone limit is 5/hour, IP limit is 3/hour
        # IP throttle should block at 3rd request
        for i in range(3):
            response = self.client.post(
                '/api/v1/accounts/otp/send/',
                {
                    'phone_number': '+254700000001',
                    'channel': 'PHONE',
                    'purpose': 'PHONE_VERIFY',
                },
                format='json',
            )
            self.assertEqual(response.status_code, status.HTTP_200_OK)

        # 4th request should be blocked by IP throttle
        response = self.client.post(
            '/api/v1/accounts/otp/send/',
            {
                'phone_number': '+254700000001',
                'channel': 'PHONE',
                'purpose': 'PHONE_VERIFY',
            },
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

    @patch('accounts.views.get_otp_backend')
    @override_settings(
        OTP_SEND_IP_RATE='20/hour',
        DEBUG=True,
        AT_API_KEY='test_key',
        AT_USERNAME='test_user'
    )
    def test_burst_under_threshold_not_falsely_blocked(self, mock_backend):
        """
        Test that a burst just under the configured IP threshold
        (simulating a shared-NAT office) is NOT falsely blocked.
        """
        mock_backend.return_value.send.return_value = None

        # Make 19 requests (limit is 20/hour)
        for i in range(19):
            response = self.client.post(
                '/api/v1/accounts/otp/send/',
                {
                    'phone_number': f'+25470000000{i % 10}',  # Cycle through 10 phones
                    'channel': 'PHONE',
                    'purpose': 'PHONE_VERIFY',
                },
                format='json',
            )
            self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_throttle_uses_configurable_setting(self):
        """
        Test that OTPSendIPThrottle reads rate from Django setting.
        """
        with override_settings(OTP_SEND_IP_RATE='50/hour'):
            throttle = OTPSendIPThrottle()
            self.assertEqual(throttle.rate, '50/hour')

    def test_throttle_uses_default_setting_when_not_configured(self):
        """
        Test that OTPSendIPThrottle uses default rate when setting is missing.
        """
        with override_settings(OTP_SEND_IP_RATE=None):
            throttle = OTPSendIPThrottle()
            self.assertEqual(throttle.rate, '20/hour')

    def test_throttle_message_matches_per_phone_throttle(self):
        """
        Test that IP throttle uses same error message as phone throttle
        to avoid leaking strategy to attackers.
        """
        from rest_framework.exceptions import Throttled

        throttle = OTPSendIPThrottle()
        with self.assertRaises(Throttled) as cm:
            throttle.throttle_failure()

        self.assertEqual(
            str(cm.exception.detail),
            'Too many OTP requests. Please try again later.'
        )

    @patch('accounts.views.get_otp_backend')
    @override_settings(
        OTP_SEND_IP_RATE='3/hour',
        DEBUG=True,
        AT_API_KEY='test_key',
        AT_USERNAME='test_user'
    )
    def test_different_ips_have_independent_limits(self, mock_backend):
        """
        Test that different IP addresses have independent throttle limits.
        """
        mock_backend.return_value.send.return_value = None

        # Make 3 requests from IP 1 (should exhaust its limit)
        for i in range(3):
            response = self.client.post(
                '/api/v1/accounts/otp/send/',
                {
                    'phone_number': f'+25470000000{i}',
                    'channel': 'PHONE',
                    'purpose': 'PHONE_VERIFY',
                },
                format='json',
                REMOTE_ADDR='192.168.1.1',
            )
            self.assertEqual(response.status_code, status.HTTP_200_OK)

        # 4th request from IP 1 should be blocked
        response = self.client.post(
            '/api/v1/accounts/otp/send/',
            {
                'phone_number': '+254700000009',
                'channel': 'PHONE',
                'purpose': 'PHONE_VERIFY',
            },
            format='json',
            REMOTE_ADDR='192.168.1.1',
        )
        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

        # Request from IP 2 should succeed (different limit)
        response = self.client.post(
            '/api/v1/accounts/otp/send/',
            {
                'phone_number': '+254700000010',
                'channel': 'PHONE',
                'purpose': 'PHONE_VERIFY',
            },
            format='json',
            REMOTE_ADDR='192.168.1.2',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
