from django.db import models
from django.utils import timezone


class JobHeartbeat(models.Model):
    """Last-run marker for a scheduled background job.

    A monitored job calls :meth:`record` at the end of every run. The
    ``/health/jobs/`` endpoint reads these rows so an external monitor
    (uptime check, alerting rule) can fire when a job stops running - the
    row simply goes stale, or is never created.
    """

    class Status(models.TextChoices):
        OK = 'OK', 'OK'
        ERROR = 'ERROR', 'Error'

    job_name = models.CharField(max_length=100, unique=True)
    last_run_at = models.DateTimeField()
    last_status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.OK,
    )
    detail = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['job_name']

    def __str__(self):
        return f'{self.job_name} @ {self.last_run_at:%Y-%m-%d %H:%M} ' \
               f'({self.last_status})'

    @classmethod
    def record(cls, job_name, status=None, detail=None):
        """Upsert the heartbeat for ``job_name`` with ``last_run_at=now``."""
        return cls.objects.update_or_create(
            job_name=job_name,
            defaults={
                'last_run_at': timezone.now(),
                'last_status': status or cls.Status.OK,
                'detail': detail or {},
            },
        )[0]
