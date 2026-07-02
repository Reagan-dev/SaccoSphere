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


