"""KYC submission and IPRS verification metrics using Redis cache.

This module provides metrics tracking for KYC operations:
- Counter: kyc_submissions_total (labeled by outcome)
- Counter: kyc_iprs_calls_total (labeled by result)
- Histogram: kyc_iprs_call_duration_seconds
- Histogram: kyc_end_to_end_processing_seconds

Metrics are stored in Redis with time-bucketed keys (YYYYMMDD format)
and a 90-day TTL to allow historical analysis.

Alert Threshold Recommendations:
- IPRS failure rate: Alert if > 5% over 5-minute window
  (indicates IPRS service degradation or configuration issues)
- IPRS call duration p95: Alert if > 10 seconds
  (indicates performance degradation or network issues)
- End-to-end processing p95: Alert if > 30 seconds
  (indicates system-wide performance issues)
"""

import time
from datetime import datetime

from django.core.cache import cache

# Redis key prefix for KYC metrics
KYC_METRICS_KEY_PREFIX = 'kyc'
# TTL for daily metrics keys (90 days to allow historical analysis)
KYC_METRICS_TTL_SECONDS = 90 * 24 * 60 * 60


def _get_metrics_date():
    """Get current date in YYYYMMDD format for time-bucketed keys."""
    return datetime.now().strftime('%Y%m%d')


def _increment_counter(metric_name: str, label_name: str, label_value: str) -> None:
    """
    Atomically increment a counter metric in Redis.

    Args:
        metric_name: Name of the metric (e.g., 'kyc_submissions_total')
        label_name: Name of the label (e.g., 'outcome')
        label_value: Value of the label (e.g., 'approved')
    """
    date_str = _get_metrics_date()
    key = f'{KYC_METRICS_KEY_PREFIX}:{metric_name}:{label_name}:{label_value}:{date_str}'
    try:
        cache.incr(key)
        cache.set(key, cache.get(key), KYC_METRICS_TTL_SECONDS)
    except ValueError:
        cache.set(key, 1, KYC_METRICS_TTL_SECONDS)


def _observe_histogram(metric_name: str, value: float, buckets: list) -> None:
    """
    Record a histogram observation in Redis.

    Since Redis doesn't have native histogram support, we store
    bucket counts as separate counters. This approximates a Prometheus
    histogram where each bucket counts observations <= that value.

    Args:
        metric_name: Name of the metric (e.g., 'kyc_iprs_call_duration_seconds')
        value: Observed value in seconds
        buckets: List of bucket boundaries (e.g., [0.1, 0.5, 1, 5, 10, 30])
    """
    date_str = _get_metrics_date()
    for bucket in buckets:
        if value <= bucket:
            bucket_key = f'{KYC_METRICS_KEY_PREFIX}:{metric_name}_bucket:{bucket}:{date_str}'
            try:
                cache.incr(bucket_key)
                cache.set(bucket_key, cache.get(bucket_key), KYC_METRICS_TTL_SECONDS)
            except ValueError:
                cache.set(bucket_key, 1, KYC_METRICS_TTL_SECONDS)


# KYC submission outcomes
KYC_OUTCOMES = ['submitted', 'approved', 'rejected', 'iprs_unavailable']

# IPRS call results
IPRS_RESULTS = ['success', 'failure', 'timeout']

# Histogram buckets for IPRS call duration (seconds)
IPRS_DURATION_BUCKETS = [0.1, 0.5, 1, 2, 5, 10, 30]

# Histogram buckets for end-to-end processing (seconds)
PROCESSING_DURATION_BUCKETS = [1, 5, 10, 30, 60, 300]


def increment_kyc_submission(outcome: str) -> None:
    """
    Increment KYC submission counter.

    Args:
        outcome: One of 'submitted', 'approved', 'rejected', 'iprs_unavailable'
    """
    if outcome not in KYC_OUTCOMES:
        raise ValueError(f'Invalid KYC outcome: {outcome}. Must be one of {KYC_OUTCOMES}')
    _increment_counter('kyc_submissions_total', 'outcome', outcome)


def increment_iprs_call(result: str) -> None:
    """
    Increment IPRS call counter.

    Args:
        result: One of 'success', 'failure', 'timeout'
    """
    if result not in IPRS_RESULTS:
        raise ValueError(f'Invalid IPRS result: {result}. Must be one of {IPRS_RESULTS}')
    _increment_counter('kyc_iprs_calls_total', 'result', result)


def observe_iprs_call_duration(duration_seconds: float) -> None:
    """
    Record IPRS call duration.

    Args:
        duration_seconds: Duration of the IPRS call in seconds
    """
    _observe_histogram('kyc_iprs_call_duration_seconds', duration_seconds, IPRS_DURATION_BUCKETS)


def observe_processing_time(duration_seconds: float) -> None:
    """
    Record end-to-end KYC processing time.

    Args:
        duration_seconds: Duration from submission to final outcome in seconds
    """
    _observe_histogram('kyc_end_to_end_processing_seconds', duration_seconds, PROCESSING_DURATION_BUCKETS)


def get_kyc_metrics(date_str: str = None) -> dict:
    """
    Get KYC submission metrics for a specific date.

    Args:
        date_str: Date in YYYYMMDD format. Defaults to today.

    Returns:
        dict: Metrics by outcome
    """
    if date_str is None:
        date_str = _get_metrics_date()

    metrics = {}
    for outcome in KYC_OUTCOMES:
        key = f'{KYC_METRICS_KEY_PREFIX}:kyc_submissions_total:outcome:{outcome}:{date_str}'
        metrics[outcome] = cache.get(key, 0)

    metrics['total'] = sum(metrics.values())
    return metrics


def get_iprs_metrics(date_str: str = None) -> dict:
    """
    Get IPRS call metrics for a specific date.

    Args:
        date_str: Date in YYYYMMDD format. Defaults to today.

    Returns:
        dict: Metrics by result
    """
    if date_str is None:
        date_str = _get_metrics_date()

    metrics = {}
    for result in IPRS_RESULTS:
        key = f'{KYC_METRICS_KEY_PREFIX}:kyc_iprs_calls_total:result:{result}:{date_str}'
        metrics[result] = cache.get(key, 0)

    metrics['total'] = sum(metrics.values())
    return metrics


def get_iprs_duration_metrics(date_str: str = None) -> dict:
    """
    Get IPRS call duration histogram metrics for a specific date.

    Args:
        date_str: Date in YYYYMMDD format. Defaults to today.

    Returns:
        dict: Bucket counts
    """
    if date_str is None:
        date_str = _get_metrics_date()

    metrics = {}
    for bucket in IPRS_DURATION_BUCKETS:
        key = f'{KYC_METRICS_KEY_PREFIX}:kyc_iprs_call_duration_seconds_bucket:{bucket}:{date_str}'
        metrics[f'le_{bucket}'] = cache.get(key, 0)

    return metrics


def get_processing_time_metrics(date_str: str = None) -> dict:
    """
    Get end-to-end processing time histogram metrics for a specific date.

    Args:
        date_str: Date in YYYYMMDD format. Defaults to today.

    Returns:
        dict: Bucket counts
    """
    if date_str is None:
        date_str = _get_metrics_date()

    metrics = {}
    for bucket in PROCESSING_DURATION_BUCKETS:
        key = f'{KYC_METRICS_KEY_PREFIX}:kyc_end_to_end_processing_seconds_bucket:{bucket}:{date_str}'
        metrics[f'le_{bucket}'] = cache.get(key, 0)

    return metrics
