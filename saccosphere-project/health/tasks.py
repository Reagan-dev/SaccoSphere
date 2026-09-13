"""In-app backstop alerting for stale or errored job heartbeats.

``/health/jobs/`` exposes heartbeat freshness as a passive 503 for an
external uptime monitor to page on. This task is the backstop for when
that external monitor is missing or misconfigured: it evaluates the
same heartbeats on a Celery beat schedule, so detection does not
depend on anything outside the app.
"""

import logging

from celery import shared_task
from django.core.cache import cache
from django.core.mail import mail_admins

from .services import evaluate_job_health

logger = logging.getLogger(__name__)

ALERT_STATE_CACHE_KEY = 'health:job_heartbeat_alert_state'
ALERT_STATE_TTL_SECONDS = 3600


@shared_task(name='health.check_job_heartbeats')
def check_job_heartbeats():
    """Alert once when a monitored job goes unhealthy, and on recovery."""
    all_healthy, jobs = evaluate_job_health()
    was_alerting = cache.get(ALERT_STATE_CACHE_KEY, False)

    if not all_healthy and not was_alerting:
        _send_alert(jobs)
        cache.set(ALERT_STATE_CACHE_KEY, True, ALERT_STATE_TTL_SECONDS)
    elif all_healthy and was_alerting:
        _send_recovery()
        cache.delete(ALERT_STATE_CACHE_KEY)


def _send_alert(jobs):
    unhealthy = {
        name: detail
        for name, detail in jobs.items()
        if detail['status'] != 'ok'
    }
    summary = ', '.join(
        f"{name} ({detail['status']})" for name, detail in unhealthy.items()
    )
    message = f'Scheduled job heartbeat check failed for: {summary}'
    logger.error(message)

    try:
        import sentry_sdk
        sentry_sdk.set_context('job_heartbeat_alert', unhealthy)
        sentry_sdk.capture_message(message, level='error')
    except ImportError:
        pass

    mail_admins(
        subject='SaccoSphere: scheduled job heartbeat check failed',
        message=message,
        fail_silently=True,
    )


def _send_recovery():
    message = (
        'Scheduled job heartbeat check recovered: all monitored jobs '
        'are healthy again.'
    )
    logger.info(message)

    try:
        import sentry_sdk
        sentry_sdk.capture_message(message, level='info')
    except ImportError:
        pass

    mail_admins(
        subject='SaccoSphere: scheduled job heartbeat check recovered',
        message=message,
        fail_silently=True,
    )
