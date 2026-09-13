"""Celery application configuration for SaccoSphere."""

import os

from celery import Celery
from celery.schedules import crontab
from django.conf import settings
from kombu import Queue


os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.production')

app = Celery('saccosphere')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.conf.timezone = settings.CELERY_TIMEZONE

app.conf.task_queues = (
    Queue('payments', queue_arguments={'x-max-priority': 10}),
    Queue('notifications', queue_arguments={'x-max-priority': 5}),
    Queue('reports', queue_arguments={'x-max-priority': 2}),
    Queue('default', queue_arguments={'x-max-priority': 3}),
)
app.conf.task_default_queue = 'default'
app.conf.task_routes = {
    'payments.tasks.process_stk_callback': {'queue': 'payments'},
    'payments.tasks.process_b2c_callback': {'queue': 'payments'},
    'payments.tasks.*': {'queue': 'payments'},
    'notifications.tasks.*': {'queue': 'notifications'},
    'ledger.tasks.*': {'queue': 'reports'},
}
app.conf.beat_schedule = {
    **settings.CELERY_BEAT_SCHEDULE,
    'cleanup-expired-otps': {
        'task': 'accounts.tasks.cleanup_expired_otps',
        'schedule': 300.0,
    },
    'hourly-sacco-liquidity-check': {
        'task': 'services.tasks.check_all_sacco_liquidity',
        'schedule': crontab(minute=0),
    },
    'daily-npl-arrears-check': {
        'task': 'services.tasks.flag_npl_arrears',
        'schedule': crontab(minute=30, hour=6),
    },
    'iprs-failure-rate-check': {
        'task': 'accounts.tasks.check_iprs_failure_rate',
        'schedule': 300.0,  # Every 5 minutes
    },
    'cleanup-expired-kyc': {
        'task': 'accounts.tasks.cleanup_expired_kyc',
        'schedule': crontab(minute=0, hour=2),  # Daily at 2 AM
    },
    'purge-expired-crb-raw-response': {
        'task': 'services.tasks.purge_expired_crb_raw_response',
        'schedule': crontab(minute=30, hour=2),  # Daily at 2:30 AM
    },
    'purge-expired-notification-content': {
        'task': 'notifications.tasks.purge_expired_notification_content',
        'schedule': crontab(minute=45, hour=2),  # Daily at 2:45 AM
    },
    'process-queued-erasure-requests': {
        'task': 'accounts.tasks.process_queued_erasure_requests',
        'schedule': crontab(minute='*/30'),  # Every 30 minutes
    },
    'flag-stuck-sms-campaigns': {
        'task': 'saccomanagement.tasks.flag_stuck_sms_campaigns',
        'schedule': crontab(minute='*/15'),  # Every 15 minutes
    },
    'expire-stale-external-guarantors': {
        'task': 'guarantor.tasks.expire_stale_external_guarantors',
        'schedule': crontab(minute='*/30'),  # Every 30 minutes
    },
    # Same reasoning as the external sweep above - an internal guarantor
    # who never responds otherwise blocks the loan forever with no
    # signal to the applicant.
    'expire-stale-internal-guarantors': {
        'task': 'services.tasks.expire_stale_internal_guarantors',
        'schedule': crontab(minute='*/30'),  # Every 30 minutes
    },
    # Flip past-due instalments to OVERDUE + accrue penalties first, then
    # send the reminders/overdue alerts that key off that status.
    'mark-overdue-instalments': {
        'task': 'services.tasks.mark_overdue_instalments',
        'schedule': crontab(minute=30, hour=5),  # Daily 05:30
    },
    'send-repayment-reminders': {
        'task': 'services.tasks.send_repayment_reminders',
        'schedule': crontab(minute=0, hour=6),  # Daily 06:00
    },
    # get_upcoming_instalments matches an exact days_ahead, so the 1-day
    # reminder needs its own run - it is never reached by the 3-day call
    # above. Safe to run alongside it: ReminderLog dedupes the overdue
    # alerts both runs also send (see send_repayment_reminders.py).
    'send-repayment-reminders-1-day': {
        'task': 'services.tasks.send_repayment_reminders',
        'schedule': crontab(minute=15, hour=6),  # Daily 06:15
        'kwargs': {'days': 1},
    },
    # Flag (never auto-fix) any Saving.amount that drifted from the
    # ledger. Runs after the other daily financial sweeps.
    'reconcile-savings-ledger': {
        'task': 'services.tasks.reconcile_savings_ledger',
        'schedule': crontab(minute=45, hour=6),  # Daily 06:45
    },
    # Credit one month of savings interest to opted-in SACCOs. Runs early
    # on the 1st; idempotent per (saving, month), so a missed/retried run
    # is harmless.
    'accrue-savings-interest': {
        'task': 'services.tasks.accrue_savings_interest',
        'schedule': crontab(minute=30, hour=1, day_of_month=1),
    },
    # Backstop for /health/jobs/: pages admins directly if a monitored
    # job's heartbeat goes stale or errored, in case the external
    # uptime monitor watching that endpoint is missing or misconfigured.
    'check-job-heartbeats': {
        'task': 'health.check_job_heartbeats',
        'schedule': crontab(minute='*/30'),
    },
}
app.conf.task_serializer = 'json'
app.conf.result_expires = 3600
app.conf.task_default_retry_delay = 60
# Only takes effect for tasks that opt into acks_late=True (the
# payment-callback and reconciliation tasks in payments/tasks.py and
# services/tasks.py) - a worker that is SIGKILLed or OOM-killed mid-task
# redelivers the message instead of losing it outright. Every task that
# opts in is idempotent by design (unique-constraint / status-guarded),
# so redelivery is a safe no-op, not a double-processing risk.
app.conf.task_reject_on_worker_lost = True
app.conf.task_annotations = {
    '*': {
        'max_retries': 3,
        'default_retry_delay': 60,
    },
}

app.autodiscover_tasks()
