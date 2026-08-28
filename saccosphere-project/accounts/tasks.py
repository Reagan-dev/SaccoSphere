"""Celery tasks for account maintenance."""

import logging
from datetime import timedelta

from celery import shared_task
from django.db.models import Q
from django.utils import timezone

from .models import OTPToken


logger = logging.getLogger(__name__)


@shared_task(name='accounts.tasks.cleanup_expired_otps')
def cleanup_expired_otps():
    """Delete expired used OTPs and abandoned unused OTPs."""
    now = timezone.now()
    abandoned_before = now - timedelta(hours=24)
    deleted_count, _ = OTPToken.objects.filter(
        Q(expires_at__lt=now, is_used=True)
        | Q(created_at__lt=abandoned_before, is_used=False)
    ).delete()

    logger.info('Deleted %s expired or abandoned OTP tokens.', deleted_count)
    return deleted_count


@shared_task(name='accounts.tasks.check_iprs_failure_rate')
def check_iprs_failure_rate():
    """
    Check IPRS failure rate and send alerts if threshold exceeded.

    This task runs periodically to monitor IPRS service health and
    send alerts via Sentry when the failure rate exceeds the configured threshold.
    """
    try:
        from accounts.iprs_alerting import check_iprs_failure_rate as check_rate
        return check_rate()
    except Exception as exc:
        logger.error('IPRS failure rate check failed: %s', exc)
        raise


@shared_task(name='accounts.tasks.cleanup_expired_kyc')
def cleanup_expired_kyc():
    """
    Clean up KYC documents past their retention period.

    This task runs periodically to find KYC records past retention_until
    and anonymize them by deleting S3 objects and PII fields while
    preserving minimal verification trail.
    """
    try:
        from django.core.management import call_command
        from io import StringIO

        output = StringIO()
        call_command('cleanup_expired_kyc', stdout=output)
        result = output.getvalue()
        logger.info('KYC retention cleanup completed: %s', result)
        return result
    except Exception as exc:
        logger.error('KYC retention cleanup failed: %s', exc)
        raise


@shared_task(name='accounts.tasks.process_queued_erasure_requests')
def process_queued_erasure_requests():
    """
    Process erasure requests that were on hold but are now eligible.

    This task runs periodically to check for erasure requests whose
    holds have expired and process them.
    """
    try:
        from accounts.kyc_retention import process_queued_erasure_requests
        process_queued_erasure_requests()
        logger.info('Queued erasure requests processed successfully')
    except Exception as exc:
        logger.error('Failed to process queued erasure requests: %s', exc)
        raise

