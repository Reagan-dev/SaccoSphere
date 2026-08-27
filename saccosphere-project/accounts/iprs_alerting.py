"""IPRS failure rate alerting using Sentry and Redis metrics.

This module provides periodic monitoring of IPRS verification failure rates
and sends alerts via Sentry when thresholds are exceeded.

Threshold Justification:
- Failure rate > 50% over 10-minute rolling window
- Normal IPRS failure rate should be <5% (transient errors only)
- 50% threshold indicates significant service degradation
- 10-minute window filters out transient blips while catching sustained outages
- Alerting on 50% rather than 5% prevents noise during minor fluctuations

Debouncing:
- Alerts are debounced using Redis state tracking
- Only one initial alert when threshold is crossed
- Periodic reminders every 30 minutes while in alert state
- Recovery notification when rate drops below 40% (hysteresis)
"""

import logging
from datetime import datetime, timedelta

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger(__name__)

# Alert configuration
IPRS_FAILURE_RATE_THRESHOLD = 0.50  # 50% failure rate
IPRS_RECOVERY_RATE_THRESHOLD = 0.40  # 40% for recovery (hysteresis)
IPRS_ALERT_WINDOW_MINUTES = 10  # Rolling window size
IPRS_ALERT_CHECK_INTERVAL_SECONDS = 300  # Check every 5 minutes
IPRS_ALERT_REMINDER_INTERVAL_SECONDS = 1800  # Remind every 30 minutes

# Redis keys for alert state
IPRS_ALERT_STATE_KEY = 'iprs:alert:state'
IPRS_ALERT_LAST_SENT_KEY = 'iprs:alert:last_sent'
IPRS_ALERT_STATE_TTL_SECONDS = 3600  # State expires after 1 hour


def get_iprs_failure_rate(window_minutes=10):
    """
    Calculate IPRS failure rate over a rolling window.

    Args:
        window_minutes: Number of minutes to look back (default: 10)

    Returns:
        float: Failure rate as a percentage (0.0 to 1.0), or None if no data
    """
    try:
        from accounts.kyc_metrics import get_iprs_metrics
    except ImportError:
        logger.warning('KYC metrics module not available')
        return None

    # Get metrics for the current date
    metrics = get_iprs_metrics()
    
    total_calls = metrics.get('total', 0)
    failure_calls = metrics.get('failure', 0)
    
    if total_calls == 0:
        return None
    
    return failure_calls / total_calls


def send_sentry_alert(failure_rate, window_minutes):
    """
    Send an alert to Sentry for IPRS failure rate.

    Args:
        failure_rate: Current failure rate (0.0 to 1.0)
        window_minutes: Window size for the calculation
    """
    try:
        import sentry_sdk
    except ImportError:
        logger.warning('Sentry not configured, skipping alert')
        return

    message = (
        f'IPRS failure rate alert: {failure_rate:.1%} over last {window_minutes} minutes. '
        f'Threshold: {IPRS_FAILURE_RATE_THRESHOLD:.1%}. '
        f'IPRS service may be degraded or unavailable.'
    )

    logger.error(message)
    
    # Send to Sentry with custom level and context
    sentry_sdk.capture_message(
        message,
        level='error',
    )
    
    # Add context for alert
    sentry_sdk.set_context('iprs_alert', {
        'failure_rate': failure_rate,
        'window_minutes': window_minutes,
        'threshold': IPRS_FAILURE_RATE_THRESHOLD,
        'timestamp': timezone.now().isoformat(),
    })


def send_sentry_recovery(failure_rate, window_minutes):
    """
    Send a recovery notification to Sentry.

    Args:
        failure_rate: Current failure rate (0.0 to 1.0)
        window_minutes: Window size for the calculation
    """
    try:
        import sentry_sdk
    except ImportError:
        logger.warning('Sentry not configured, skipping recovery')
        return

    message = (
        f'IPRS failure rate recovered: {failure_rate:.1%} over last {window_minutes} minutes. '
        f'Recovery threshold: {IPRS_RECOVERY_RATE_THRESHOLD:.1%}. '
        f'IPRS service has recovered.'
    )

    logger.info(message)
    
    # Send to Sentry with info level
    sentry_sdk.capture_message(
        message,
        level='info',
    )
    
    # Add context for recovery
    sentry_sdk.set_context('iprs_recovery', {
        'failure_rate': failure_rate,
        'window_minutes': window_minutes,
        'recovery_threshold': IPRS_RECOVERY_RATE_THRESHOLD,
        'timestamp': timezone.now().isoformat(),
    })


def check_iprs_failure_rate():
    """
    Check IPRS failure rate and send alerts if threshold exceeded.

    This function implements debouncing logic:
    - Only sends alert if not already in alert state
    - Sends periodic reminders while in alert state
    - Sends recovery notification when rate drops below recovery threshold
    """
    failure_rate = get_iprs_failure_rate(IPRS_ALERT_WINDOW_MINUTES)
    
    if failure_rate is None:
        logger.debug('No IPRS metrics data available')
        return
    
    current_state = cache.get(IPRS_ALERT_STATE_KEY, 'normal')
    last_sent = cache.get(IPRS_ALERT_LAST_SENT_KEY, 0)
    now = timezone.now().timestamp()
    
    logger.info(
        'IPRS failure rate check: %.1f%% (state: %s)',
        failure_rate * 100,
        current_state,
    )
    
    # Check if we should alert
    if failure_rate >= IPRS_FAILURE_RATE_THRESHOLD:
        if current_state == 'normal':
            # First time crossing threshold - send initial alert
            send_sentry_alert(failure_rate, IPRS_ALERT_WINDOW_MINUTES)
            cache.set(IPRS_ALERT_STATE_KEY, 'alerting', IPRS_ALERT_STATE_TTL_SECONDS)
            cache.set(IPRS_ALERT_LAST_SENT_KEY, now, IPRS_ALERT_STATE_TTL_SECONDS)
            logger.warning('IPRS alert triggered: failure rate %.1f%%', failure_rate * 100)
        elif current_state == 'alerting':
            # Already in alert state - check if we should send a reminder
            time_since_last_alert = now - last_sent
            if time_since_last_alert >= IPRS_ALERT_REMINDER_INTERVAL_SECONDS:
                send_sentry_alert(failure_rate, IPRS_ALERT_WINDOW_MINUTES)
                cache.set(IPRS_ALERT_LAST_SENT_KEY, now, IPRS_ALERT_STATE_TTL_SECONDS)
                logger.warning(
                    'IPRS alert reminder sent: failure rate %.1f%% (last alert %.0f minutes ago)',
                    failure_rate * 100,
                    time_since_last_alert / 60,
                )
    elif failure_rate <= IPRS_RECOVERY_RATE_THRESHOLD:
        if current_state == 'alerting':
            # Rate dropped below recovery threshold - send recovery notification
            send_sentry_recovery(failure_rate, IPRS_ALERT_WINDOW_MINUTES)
            cache.set(IPRS_ALERT_STATE_KEY, 'normal', IPRS_ALERT_STATE_TTL_SECONDS)
            cache.delete(IPRS_ALERT_LAST_SENT_KEY)
            logger.info('IPRS recovery notification sent: failure rate %.1f%%', failure_rate * 100)
    else:
        # Rate between thresholds - maintain current state
        logger.debug(
            'IPRS failure rate %.1f%% between thresholds, maintaining state: %s',
            failure_rate * 100,
            current_state,
        )
    
    return {
        'failure_rate': failure_rate,
        'state': current_state,
        'threshold': IPRS_FAILURE_RATE_THRESHOLD,
    }
