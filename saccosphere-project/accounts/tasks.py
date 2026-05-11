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


# ============================================================
# REVIEW - READ THIS THEN DELETE FROM THIS LINE TO THE END
# ============================================================
#
# config/celery.py
#
# This file creates the Celery app for SaccoSphere. Celery is the background
# job system. It loads Django settings, discovers tasks.py files in installed
# apps, defines named queues, routes tasks to those queues, and schedules the
# OTP cleanup task every five minutes.
#
# accounts/tasks.py
#
# cleanup_expired_otps deletes OTP records that no longer need to stay in the
# database. It removes used tokens after they expire and also removes abandoned
# unused tokens older than 24 hours. It logs and returns the number deleted.
#
# config/settings/base.py
#
# The Celery settings point Celery at Redis for both the broker and result
# backend. The broker stores pending jobs. The result backend stores task
# results for a short time. JSON serialization keeps task payloads portable and
# safer than Python pickle.
#
# render.yaml
#
# The web service still runs the Django API. The new worker service runs Celery
# workers for payments, notifications, reports, and default queues. The beat
# service runs the scheduler that sends periodic tasks such as OTP cleanup.
#
# requirements.txt
#
# celery[redis] installs Celery plus Redis transport support. redis installs
# the Redis Python client. django-celery-beat adds database-backed periodic
# schedules for Celery beat.
#
# Django/Python concepts you might not know well
#
# A Celery worker is a separate process from Django. Django accepts the API
# request, then Celery handles slow or scheduled background work.
#
# A Celery queue is a named lane for tasks. Separate queues let high-priority
# payment work run apart from lower-priority reports.
#
# Celery beat is the scheduler. It wakes up on a schedule and sends tasks to
# the worker queue.
#
# Q objects let Django combine filters with OR logic. The cleanup task uses Q
# to delete either expired used tokens or abandoned unused tokens in one query.
#
# One manual test
#
# Run Redis locally, then start:
# celery -A config.celery worker -Q payments,notifications,reports,default -l info
# In another terminal, run:
# python manage.py shell -c "from accounts.tasks import cleanup_expired_otps; cleanup_expired_otps.delay()"
# Confirm the worker receives and completes the task.
#
# Important design decision
#
# OTP cleanup is periodic instead of request-driven. That keeps normal API
# requests fast and lets housekeeping run regularly even when no user is
# actively hitting OTP endpoints.
#
# END OF REVIEW - DELETE EVERYTHING FROM THE FIRST # LINE ABOVE
# ============================================================
