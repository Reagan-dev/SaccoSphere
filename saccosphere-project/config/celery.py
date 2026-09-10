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
    # Flag (never auto-fix) any Saving.amount that drifted from the
    # ledger. Runs after the other daily financial sweeps.
    'reconcile-savings-ledger': {
        'task': 'services.tasks.reconcile_savings_ledger',
        'schedule': crontab(minute=45, hour=6),  # Daily 06:45
    },
}
app.conf.task_serializer = 'json'
app.conf.result_expires = 3600
app.conf.task_default_retry_delay = 60
app.conf.task_annotations = {
    '*': {
        'max_retries': 3,
        'default_retry_delay': 60,
    },
}

app.autodiscover_tasks()
