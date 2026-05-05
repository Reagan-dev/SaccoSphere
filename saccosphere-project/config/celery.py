import os

from celery import Celery
from kombu import Queue


os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.production')

app = Celery('saccosphere')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.conf.task_queues = (
    Queue('payments', queue_arguments={'x-max-priority': 10}),
    Queue('notifications', queue_arguments={'x-max-priority': 5}),
    Queue('reports', queue_arguments={'x-max-priority': 2}),
    Queue('default', queue_arguments={'x-max-priority': 3}),
)
app.conf.task_default_queue = 'default'
app.conf.beat_schedule = {}
app.autodiscover_tasks()
