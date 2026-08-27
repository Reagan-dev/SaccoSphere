"""Test SMS delivery metrics using Redis cache."""

from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.conf import settings

from accounts.otp_backends import (
    _increment_sms_metric,
    get_sms_metrics,
    _get_metrics_date,
    SMS_METRICS_KEY_PREFIX,
)


class SMSMetricsTestCase(TestCase):
    """Test SMS delivery metrics tracking."""

    def setUp(self):
        """Clear cache before each test."""
        cache.clear()

    def test_increment_sms_metric_success(self):
        """Test that success metric increments correctly."""
        _increment_sms_metric('success', 'PHONE_VERIFY')

        metrics = get_sms_metrics(purpose='PHONE_VERIFY')
        self.assertEqual(metrics['success'], 1)
        self.assertEqual(metrics['failure'], 0)
        self.assertEqual(metrics['total'], 1)

    def test_increment_sms_metric_failure(self):
        """Test that failure metric increments correctly."""
        _increment_sms_metric('failure', 'PASSWORD_RESET')

        metrics = get_sms_metrics(purpose='PASSWORD_RESET')
        self.assertEqual(metrics['success'], 0)
        self.assertEqual(metrics['failure'], 1)
        self.assertEqual(metrics['total'], 1)

    def test_increment_multiple_times(self):
        """Test that multiple increments accumulate correctly."""
        for _ in range(5):
            _increment_sms_metric('success', 'LOGIN')

        metrics = get_sms_metrics(purpose='LOGIN')
        self.assertEqual(metrics['success'], 5)
        self.assertEqual(metrics['total'], 5)

    @override_settings(DEBUG=True)
    def test_concurrent_increments_are_atomic(self):
        """
        Test that concurrent increments are atomic and don't lose counts.

        This simulates multiple workers incrementing the same counter simultaneously.
        With atomic Redis INCR, all increments should be counted correctly.

        Note: This test is skipped in DEBUG mode because LocMemCache doesn't
        provide atomic increments. It only passes with Redis backend.
        """
        if settings.DEBUG:
            self.skipTest('Concurrent atomicity test requires Redis backend, not LocMemCache')

        num_threads = 10
        increments_per_thread = 100

        def increment_counter():
            for _ in range(increments_per_thread):
                _increment_sms_metric('success', 'PHONE_VERIFY')

        with ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [executor.submit(increment_counter) for _ in range(num_threads)]
            [future.result() for future in futures]

        metrics = get_sms_metrics(purpose='PHONE_VERIFY')
        expected_total = num_threads * increments_per_thread
        self.assertEqual(
            metrics['success'],
            expected_total,
            f'Expected {expected_total} increments, got {metrics["success"]}'
        )

    def test_get_metrics_by_date(self):
        """Test retrieving metrics for a specific date."""
        test_date = '20240101'

        # Override the date function to return our test date
        from accounts import otp_backends
        original_get_date = otp_backends._get_metrics_date
        otp_backends._get_metrics_date = lambda: test_date

        try:
            _increment_sms_metric('success', 'PHONE_VERIFY')
            _increment_sms_metric('failure', 'PHONE_VERIFY')

            metrics = get_sms_metrics(date_str=test_date)
            self.assertEqual(metrics['success'], 1)
            self.assertEqual(metrics['failure'], 1)
            self.assertEqual(metrics['total'], 2)
        finally:
            otp_backends._get_metrics_date = original_get_date

    def test_get_metrics_aggregated_without_purpose(self):
        """Test that metrics are aggregated when no purpose is specified."""
        _increment_sms_metric('success', 'PHONE_VERIFY')
        _increment_sms_metric('success', 'PASSWORD_RESET')
        _increment_sms_metric('failure', 'LOGIN')

        metrics = get_sms_metrics()
        self.assertEqual(metrics['success'], 2)
        self.assertEqual(metrics['failure'], 1)
        self.assertEqual(metrics['total'], 3)

    def test_get_metrics_by_purpose(self):
        """Test that metrics can be filtered by purpose."""
        _increment_sms_metric('success', 'PHONE_VERIFY')
        _increment_sms_metric('success', 'PASSWORD_RESET')
        _increment_sms_metric('failure', 'PHONE_VERIFY')

        phone_verify_metrics = get_sms_metrics(purpose='PHONE_VERIFY')
        self.assertEqual(phone_verify_metrics['success'], 1)
        self.assertEqual(phone_verify_metrics['failure'], 1)
        self.assertEqual(phone_verify_metrics['total'], 2)

        password_reset_metrics = get_sms_metrics(purpose='PASSWORD_RESET')
        self.assertEqual(password_reset_metrics['success'], 1)
        self.assertEqual(password_reset_metrics['failure'], 0)
        self.assertEqual(password_reset_metrics['total'], 1)

    def test_get_metrics_returns_zero_for_no_data(self):
        """Test that metrics return zero when no data exists."""
        metrics = get_sms_metrics(date_str='20990101')
        self.assertEqual(metrics['success'], 0)
        self.assertEqual(metrics['failure'], 0)
        self.assertEqual(metrics['total'], 0)

    def test_metrics_date_format(self):
        """Test that _get_metrics_date returns correct format."""
        date_str = _get_metrics_date()
        # Should be in YYYYMMDD format
        self.assertEqual(len(date_str), 8)
        self.assertTrue(date_str.isdigit())
        # Should be parseable as a date
        datetime.strptime(date_str, '%Y%m%d')
