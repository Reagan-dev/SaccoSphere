"""Celery application configuration for SaccoSphere."""

import os

from celery import Celery
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
    'cleanup-expired-otps': {
        'task': 'accounts.tasks.cleanup_expired_otps',
        'schedule': 300.0,
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
