"""Test IPRS failure rate alerting logic."""

from datetime import datetime
from unittest.mock import patch, Mock
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.iprs_alerting import (
    get_iprs_failure_rate,
    send_sentry_alert,
    send_sentry_recovery,
    check_iprs_failure_rate,
    IPRS_FAILURE_RATE_THRESHOLD,
    IPRS_RECOVERY_RATE_THRESHOLD,
    IPRS_ALERT_WINDOW_MINUTES,
    IPRS_ALERT_REMINDER_INTERVAL_SECONDS,
    IPRS_ALERT_STATE_KEY,
    IPRS_ALERT_LAST_SENT_KEY,
)


class IPRSAlertingTestCase(TestCase):
    """Test IPRS failure rate alerting logic."""

    def setUp(self):
        """Clear cache before each test."""
        cache.clear()

    def test_get_iprs_failure_rate_with_data(self):
        """Test failure rate calculation with metrics data."""
        with patch('accounts.kyc_metrics.get_iprs_metrics') as mock_metrics:
            mock_metrics.return_value = {
                'success': 5,
                'failure': 5,
                'total': 10,
            }

            rate = get_iprs_failure_rate()
            self.assertEqual(rate, 0.5)

    def test_get_iprs_failure_rate_no_data(self):
        """Test failure rate calculation with no metrics data."""
        with patch('accounts.kyc_metrics.get_iprs_metrics') as mock_metrics:
            mock_metrics.return_value = {'total': 0}

            rate = get_iprs_failure_rate()
            self.assertIsNone(rate)

    def test_get_iprs_failure_rate_no_failures(self):
        """Test failure rate calculation with no failures."""
        with patch('accounts.kyc_metrics.get_iprs_metrics') as mock_metrics:
            mock_metrics.return_value = {
                'success': 10,
                'failure': 0,
                'total': 10,
            }

            rate = get_iprs_failure_rate()
            self.assertEqual(rate, 0.0)

    def test_send_sentry_alert(self):
        """Test that Sentry alert is sent correctly."""
        from unittest.mock import MagicMock
        import sys
        
        mock_sentry = MagicMock()
        mock_sentry.capture_message = MagicMock()
        mock_sentry.set_context = MagicMock()
        
        # Add mock to sys.modules before the function imports it
        sys.modules['sentry_sdk'] = mock_sentry
        
        try:
            send_sentry_alert(0.6, 10)

            # Verify capture_message was called
            mock_sentry.capture_message.assert_called_once()
            call_args = mock_sentry.capture_message.call_args
            
            # Check the message contains expected information
            message = call_args[0][0]
            self.assertIn('60.0%', message)
            self.assertIn('50.0%', message)
            self.assertIn('10 minutes', message)
            
            # Check level is error
            self.assertEqual(call_args[1]['level'], 'error')
        finally:
            # Clean up
            if 'sentry_sdk' in sys.modules:
                del sys.modules['sentry_sdk']

    def test_send_sentry_alert_without_sentry(self):
        """Test that alert is skipped when Sentry is not configured."""
        import sys
        
        # Ensure sentry_sdk is not in sys.modules
        if 'sentry_sdk' in sys.modules:
            del sys.modules['sentry_sdk']
        
        # Should not raise an exception
        send_sentry_alert(0.6, 10)

    def test_send_sentry_recovery(self):
        """Test that Sentry recovery notification is sent correctly."""
        from unittest.mock import MagicMock
        import sys
        
        mock_sentry = MagicMock()
        mock_sentry.capture_message = MagicMock()
        mock_sentry.set_context = MagicMock()
        
        # Add mock to sys.modules before the function imports it
        sys.modules['sentry_sdk'] = mock_sentry
        
        try:
            send_sentry_recovery(0.3, 10)

            # Verify capture_message was called
            mock_sentry.capture_message.assert_called_once()
            call_args = mock_sentry.capture_message.call_args
            
            # Check the message contains expected information
            message = call_args[0][0]
            self.assertIn('30.0%', message)
            self.assertIn('40.0%', message)
            self.assertIn('recovered', message)
            
            # Check level is info
            self.assertEqual(call_args[1]['level'], 'info')
        finally:
            # Clean up
            if 'sentry_sdk' in sys.modules:
                del sys.modules['sentry_sdk']

    def test_send_sentry_recovery_without_sentry(self):
        """Test that recovery is skipped when Sentry is not configured."""
        import sys
        
        # Ensure sentry_sdk is not in sys.modules
        if 'sentry_sdk' in sys.modules:
            del sys.modules['sentry_sdk']
        
        # Should not raise an exception
        send_sentry_recovery(0.3, 10)


class IPRSAlertingDebouncingTestCase(TestCase):
    """Test IPRS alerting debouncing logic."""

    def setUp(self):
        """Clear cache before each test."""
        cache.clear()

    def test_crossing_threshold_triggers_single_alert(self):
        """Test that crossing the threshold triggers exactly one alert."""
        with patch('accounts.iprs_alerting.get_iprs_failure_rate') as mock_rate:
            with patch('accounts.iprs_alerting.send_sentry_alert') as mock_alert:
                mock_rate.return_value = 0.6  # Above 50% threshold

                result = check_iprs_failure_rate()

                # Verify alert was sent
                mock_alert.assert_called_once()
                
                # Verify state is set to alerting
                self.assertEqual(cache.get(IPRS_ALERT_STATE_KEY), 'alerting')
                
                # Verify last sent timestamp was set
                self.assertIsNotNone(cache.get(IPRS_ALERT_LAST_SENT_KEY))

    def test_staying_above_threshold_no_spam(self):
        """Test that staying above threshold doesn't spam alerts."""
        # Set initial alert state
        cache.set(IPRS_ALERT_STATE_KEY, 'alerting')
        cache.set(IPRS_ALERT_LAST_SENT_KEY, timezone.now().timestamp())

        with patch('accounts.iprs_alerting.get_iprs_failure_rate') as mock_rate:
            with patch('accounts.iprs_alerting.send_sentry_alert') as mock_alert:
                mock_rate.return_value = 0.6  # Still above threshold

                result = check_iprs_failure_rate()

                # Verify alert was NOT sent (debounced)
                mock_alert.assert_not_called()
                
                # State remains alerting
                self.assertEqual(cache.get(IPRS_ALERT_STATE_KEY), 'alerting')

    def test_staying_above_threshold_sends_reminder(self):
        """Test that periodic reminders are sent while above threshold."""
        # Set initial alert state with old timestamp
        old_timestamp = timezone.now().timestamp() - IPRS_ALERT_REMINDER_INTERVAL_SECONDS - 1
        cache.set(IPRS_ALERT_STATE_KEY, 'alerting')
        cache.set(IPRS_ALERT_LAST_SENT_KEY, old_timestamp)

        with patch('accounts.iprs_alerting.get_iprs_failure_rate') as mock_rate:
            with patch('accounts.iprs_alerting.send_sentry_alert') as mock_alert:
                mock_rate.return_value = 0.6  # Still above threshold

                result = check_iprs_failure_rate()

                # Verify reminder alert was sent
                mock_alert.assert_called_once()
                
                # Verify last sent timestamp was updated
                new_timestamp = cache.get(IPRS_ALERT_LAST_SENT_KEY)
                self.assertGreater(new_timestamp, old_timestamp)

    def test_recovery_below_threshold_sends_notification(self):
        """Test that recovery below threshold sends notification."""
        # Set initial alert state
        cache.set(IPRS_ALERT_STATE_KEY, 'alerting')
        cache.set(IPRS_ALERT_LAST_SENT_KEY, timezone.now().timestamp())

        with patch('accounts.iprs_alerting.get_iprs_failure_rate') as mock_rate:
            with patch('accounts.iprs_alerting.send_sentry_recovery') as mock_recovery:
                mock_rate.return_value = 0.3  # Below 40% recovery threshold

                result = check_iprs_failure_rate()

                # Verify recovery notification was sent
                mock_recovery.assert_called_once()
                
                # Verify state is reset to normal
                self.assertEqual(cache.get(IPRS_ALERT_STATE_KEY), 'normal')
                
                # Verify last sent timestamp was deleted
                self.assertIsNone(cache.get(IPRS_ALERT_LAST_SENT_KEY))

    def test_between_thresholds_maintains_state(self):
        """Test that rate between thresholds maintains current state."""
        # Set initial alert state
        cache.set(IPRS_ALERT_STATE_KEY, 'alerting')
        cache.set(IPRS_ALERT_LAST_SENT_KEY, timezone.now().timestamp())

        with patch('accounts.iprs_alerting.get_iprs_failure_rate') as mock_rate:
            with patch('accounts.iprs_alerting.send_sentry_alert') as mock_alert:
                with patch('accounts.iprs_alerting.send_sentry_recovery') as mock_recovery:
                    mock_rate.return_value = 0.45  # Between 40% and 50%

                    result = check_iprs_failure_rate()

                    # Verify no alerts were sent
                    mock_alert.assert_not_called()
                    mock_recovery.assert_not_called()
                    
                    # State remains alerting
                    self.assertEqual(cache.get(IPRS_ALERT_STATE_KEY), 'alerting')

    def test_no_data_returns_none(self):
        """Test that no metrics data returns None without alerting."""
        with patch('accounts.iprs_alerting.get_iprs_failure_rate') as mock_rate:
            mock_rate.return_value = None

            result = check_iprs_failure_rate()

            # Result should be None
            self.assertIsNone(result)
            
            # No state changes
            self.assertIsNone(cache.get(IPRS_ALERT_STATE_KEY))

    def test_hysteresis_prevents_flapping(self):
        """Test that hysteresis prevents alert flapping near threshold."""
        # Start in normal state
        self.assertIsNone(cache.get(IPRS_ALERT_STATE_KEY))

        with patch('accounts.iprs_alerting.get_iprs_failure_rate') as mock_rate:
            with patch('accounts.iprs_alerting.send_sentry_alert') as mock_alert:
                with patch('accounts.iprs_alerting.send_sentry_recovery') as mock_recovery:
                    # First check: just above threshold (51%)
                    mock_rate.return_value = 0.51
                    check_iprs_failure_rate()
                    
                    # Verify alert was sent
                    mock_alert.assert_called_once()
                    
                    # Second check: just below alert threshold but above recovery (45%)
                    mock_rate.return_value = 0.45
                    mock_alert.reset_mock()
                    check_iprs_failure_rate()
                    
                    # Verify no recovery (still above recovery threshold)
                    mock_recovery.assert_not_called()
                    mock_alert.assert_not_called()
                    
                    # State remains alerting
                    self.assertEqual(cache.get(IPRS_ALERT_STATE_KEY), 'alerting')

    def test_full_alert_cycle(self):
        """Test a complete alert cycle: alert -> remind -> recover."""
        with patch('accounts.iprs_alerting.get_iprs_failure_rate') as mock_rate:
            with patch('accounts.iprs_alerting.send_sentry_alert') as mock_alert:
                with patch('accounts.iprs_alerting.send_sentry_recovery') as mock_recovery:
                    # Phase 1: Normal -> Alert
                    mock_rate.return_value = 0.6
                    check_iprs_failure_rate()
                    
                    self.assertEqual(mock_alert.call_count, 1)
                    self.assertEqual(cache.get(IPRS_ALERT_STATE_KEY), 'alerting')
                    
                    # Phase 2: Stay alerting (no reminder yet)
                    mock_rate.return_value = 0.6
                    mock_alert.reset_mock()
                    check_iprs_failure_rate()
                    
                    self.assertEqual(mock_alert.call_count, 0)
                    
                    # Phase 3: Send reminder (simulate time passed)
                    old_timestamp = timezone.now().timestamp() - IPRS_ALERT_REMINDER_INTERVAL_SECONDS - 1
                    cache.set(IPRS_ALERT_LAST_SENT_KEY, old_timestamp)
                    mock_rate.return_value = 0.6
                    check_iprs_failure_rate()
                    
                    self.assertEqual(mock_alert.call_count, 1)
                    
                    # Phase 4: Recover
                    mock_rate.return_value = 0.3
                    check_iprs_failure_rate()
                    
                    self.assertEqual(mock_recovery.call_count, 1)
                    self.assertEqual(cache.get(IPRS_ALERT_STATE_KEY), 'normal')
                    
                    # Phase 5: Normal state maintained
                    mock_rate.return_value = 0.2
                    mock_recovery.reset_mock()
                    check_iprs_failure_rate()
                    
                    self.assertEqual(mock_recovery.call_count, 0)
                    self.assertEqual(cache.get(IPRS_ALERT_STATE_KEY), 'normal')


class IPRSTaskIntegrationTestCase(TestCase):
    """Test IPRS alerting Celery task integration."""

    def setUp(self):
        """Clear cache before each test."""
        cache.clear()

    def test_task_calls_check_function(self):
        """Test that the Celery task calls the check function."""
        from accounts.tasks import check_iprs_failure_rate as task

        with patch('accounts.iprs_alerting.check_iprs_failure_rate') as mock_check:
            mock_check.return_value = {'failure_rate': 0.5, 'state': 'normal'}
            
            result = task()
            
            # Verify the check function was called
            mock_check.assert_called_once()
            
            # Verify result is returned
            self.assertEqual(result['failure_rate'], 0.5)

    def test_task_handles_exceptions(self):
        """Test that the Celery task handles exceptions gracefully."""
        from accounts.tasks import check_iprs_failure_rate as task

        with patch('accounts.iprs_alerting.check_iprs_failure_rate') as mock_check:
            mock_check.side_effect = Exception('Test error')
            
            # Should raise the exception (Celery will handle retry)
            with self.assertRaises(Exception):
                task()
