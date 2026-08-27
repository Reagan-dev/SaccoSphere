"""Tests for KYC upload rate limiting."""

from django.test import TestCase, override_settings
from django.core.cache import cache
from rest_framework.test import APIRequestFactory
from rest_framework import status
from django.contrib.auth import get_user_model
from unittest.mock import MagicMock
from rest_framework.exceptions import Throttled
import io

from accounts.throttles import KYCUploadUserThrottle, KYCUploadIPThrottle

User = get_user_model()


class KYCUploadThrottleTestCase(TestCase):
    """Test rate limiting for KYC upload requests."""

    def setUp(self):
        """Set up test client, user, and clear cache."""
        self.factory = APIRequestFactory()
        cache.clear()
        self.user = User.objects.create_user(
            phone_number='+254700000001',
            email='test@example.com',
        )

    @override_settings(
        KYC_UPLOAD_USER_RATE='3/hour',
        KYC_UPLOAD_IP_RATE='20/hour',
    )
    def test_requests_under_user_limit_succeed(self):
        """
        Test that requests under the user limit succeed.
        """
        throttle = KYCUploadUserThrottle()
        view = MagicMock()

        # Make 3 requests (limit is 3/hour)
        for i in range(3):
            request = self.factory.post('/api/v1/accounts/kyc/upload/')
            request.user = self.user
            self.assertTrue(throttle.allow_request(request, view))

    @override_settings(
        KYC_UPLOAD_USER_RATE='3/hour',
        KYC_UPLOAD_IP_RATE='20/hour',
    )
    def test_requests_exceeding_user_limit_blocked(self):
        """
        Test that requests exceeding the user limit are blocked.
        """
        throttle = KYCUploadUserThrottle()
        view = MagicMock()

        # Make 3 requests (limit is 3/hour)
        for i in range(3):
            request = self.factory.post('/api/v1/accounts/kyc/upload/')
            request.user = self.user
            self.assertTrue(throttle.allow_request(request, view))

        # 4th request should be throttled
        request = self.factory.post('/api/v1/accounts/kyc/upload/')
        request.user = self.user
        with self.assertRaises(Throttled):
            throttle.allow_request(request, view)

    @override_settings(
        KYC_UPLOAD_USER_RATE='10/hour',
        KYC_UPLOAD_IP_RATE='3/hour',
    )
    def test_requests_exceeding_ip_limit_blocked(self):
        """
        Test that requests exceeding the IP limit are blocked.
        """
        throttle = KYCUploadIPThrottle()
        view = MagicMock()

        # Make 3 requests (IP limit is 3/hour)
        for i in range(3):
            request = self.factory.post(
                '/api/v1/accounts/kyc/upload/',
                REMOTE_ADDR='192.168.1.1',
            )
            request.user = self.user
            self.assertTrue(throttle.allow_request(request, view))

        # 4th request should be throttled by IP limit
        request = self.factory.post(
            '/api/v1/accounts/kyc/upload/',
            REMOTE_ADDR='192.168.1.1',
        )
        request.user = self.user
        with self.assertRaises(Throttled):
            throttle.allow_request(request, view)

    @override_settings(
        KYC_UPLOAD_USER_RATE='10/hour',
        KYC_UPLOAD_IP_RATE='3/hour',
    )
    def test_different_users_same_ip_share_ip_limit(self):
        """
        Test that different users from the same IP share the IP limit.
        """
        user2 = User.objects.create_user(
            phone_number='+254700000002',
            email='test2@example.com',
        )
        throttle = KYCUploadIPThrottle()
        view = MagicMock()

        # Make 3 requests from user1 (exhausts IP limit)
        for i in range(3):
            request = self.factory.post(
                '/api/v1/accounts/kyc/upload/',
                REMOTE_ADDR='192.168.1.1',
            )
            request.user = self.user
            self.assertTrue(throttle.allow_request(request, view))

        # Request from user2 (same IP) should be blocked by IP limit
        request = self.factory.post(
            '/api/v1/accounts/kyc/upload/',
            REMOTE_ADDR='192.168.1.1',
        )
        request.user = user2
        with self.assertRaises(Throttled):
            throttle.allow_request(request, view)

    @override_settings(
        KYC_UPLOAD_USER_RATE='3/hour',
        KYC_UPLOAD_IP_RATE='20/hour',
    )
    def test_different_users_have_independent_user_limits(self):
        """
        Test that different users have independent user limits.
        """
        user2 = User.objects.create_user(
            phone_number='+254700000002',
            email='test2@example.com',
        )
        throttle = KYCUploadUserThrottle()
        view = MagicMock()

        # Make 3 requests from user1 (exhausts user limit)
        for i in range(3):
            request = self.factory.post('/api/v1/accounts/kyc/upload/')
            request.user = self.user
            self.assertTrue(throttle.allow_request(request, view))

        # Request from user1 should be blocked by user limit
        request = self.factory.post('/api/v1/accounts/kyc/upload/')
        request.user = self.user
        with self.assertRaises(Throttled):
            throttle.allow_request(request, view)

        # Request from user2 should succeed (different user limit)
        request = self.factory.post('/api/v1/accounts/kyc/upload/')
        request.user = user2
        self.assertTrue(throttle.allow_request(request, view))

    @override_settings(
        KYC_UPLOAD_USER_RATE='3/hour',
        KYC_UPLOAD_IP_RATE='20/hour',
    )
    def test_limit_resets_after_window(self):
        """
        Test that the limit resets after the configured window.
        Uses cache manipulation to simulate time passing.
        """
        throttle = KYCUploadUserThrottle()
        view = MagicMock()

        # Make 3 requests (exhausts user limit)
        for i in range(3):
            request = self.factory.post('/api/v1/accounts/kyc/upload/')
            request.user = self.user
            self.assertTrue(throttle.allow_request(request, view))

        # 4th request should be throttled
        request = self.factory.post('/api/v1/accounts/kyc/upload/')
        request.user = self.user
        with self.assertRaises(Throttled):
            throttle.allow_request(request, view)

        # Clear the cache to simulate time passing (window reset)
        cache.clear()

        # Request should now succeed
        request = self.factory.post('/api/v1/accounts/kyc/upload/')
        request.user = self.user
        self.assertTrue(throttle.allow_request(request, view))

    def test_user_throttle_uses_configurable_setting(self):
        """
        Test that KYCUploadUserThrottle reads rate from Django setting.
        """
        with override_settings(KYC_UPLOAD_USER_RATE='50/hour'):
            throttle = KYCUploadUserThrottle()
            self.assertEqual(throttle.rate, '50/hour')

    def test_user_throttle_uses_default_setting_when_not_configured(self):
        """
        Test that KYCUploadUserThrottle uses default rate when setting is missing.
        """
        with override_settings(KYC_UPLOAD_USER_RATE=None):
            throttle = KYCUploadUserThrottle()
            self.assertEqual(throttle.rate, '10/hour')

    def test_ip_throttle_uses_configurable_setting(self):
        """
        Test that KYCUploadIPThrottle reads rate from Django setting.
        """
        with override_settings(KYC_UPLOAD_IP_RATE='50/hour'):
            throttle = KYCUploadIPThrottle()
            self.assertEqual(throttle.rate, '50/hour')

    def test_ip_throttle_uses_default_setting_when_not_configured(self):
        """
        Test that KYCUploadIPThrottle uses default rate when setting is missing.
        """
        with override_settings(KYC_UPLOAD_IP_RATE=None):
            throttle = KYCUploadIPThrottle()
            self.assertEqual(throttle.rate, '20/hour')

    @override_settings(
        KYC_UPLOAD_USER_RATE='3/hour',
        KYC_UPLOAD_IP_RATE='20/hour',
    )
    def test_throttle_logs_when_triggered(self):
        """
        Test that throttling is logged when triggered.
        """
        throttle = KYCUploadUserThrottle()
        view = MagicMock()

        # Make 3 requests (exhausts user limit)
        for i in range(3):
            request = self.factory.post('/api/v1/accounts/kyc/upload/')
            request.user = self.user
            self.assertTrue(throttle.allow_request(request, view))

        # 4th request should be throttled and logged
        with self.assertLogs('accounts.throttles', level='WARNING') as log:
            request = self.factory.post('/api/v1/accounts/kyc/upload/')
            request.user = self.user
            with self.assertRaises(Throttled):
                throttle.allow_request(request, view)
            # Check that a warning was logged
            self.assertTrue(
                any('KYC upload throttled' in message for message in log.output)
            )

    @override_settings(
        KYC_UPLOAD_USER_RATE='10/hour',
        KYC_UPLOAD_IP_RATE='3/hour',
    )
    def test_ip_throttle_logs_when_triggered(self):
        """
        Test that IP throttling is logged when triggered.
        """
        throttle = KYCUploadIPThrottle()
        view = MagicMock()

        # Make 3 requests (exhausts IP limit)
        for i in range(3):
            request = self.factory.post(
                '/api/v1/accounts/kyc/upload/',
                REMOTE_ADDR='192.168.1.1',
            )
            request.user = self.user
            self.assertTrue(throttle.allow_request(request, view))

        # 4th request should be throttled by IP and logged
        with self.assertLogs('accounts.throttles', level='WARNING') as log:
            request = self.factory.post(
                '/api/v1/accounts/kyc/upload/',
                REMOTE_ADDR='192.168.1.1',
            )
            request.user = self.user
            with self.assertRaises(Throttled):
                throttle.allow_request(request, view)
            # Check that a warning was logged
            self.assertTrue(
                any('KYC upload throttled' in message for message in log.output)
            )
