"""Shared job-heartbeat evaluation.

Used by both the ``/health/jobs/`` endpoint (for an external monitor to
poll) and the in-app alerting task in :mod:`health.tasks` (a backstop in
case that external monitor is ever missing or misconfigured).
"""

from django.utils import timezone

from .models import JobHeartbeat
from .monitored_jobs import MONITORED_JOBS


def evaluate_job_health():
    """Return ``(all_healthy, jobs)`` for every entry in MONITORED_JOBS.

    ``jobs`` maps job name to a dict describing its status - one of
    'ok', 'stale', 'errored', or 'missing'.
    """
    now = timezone.now()
    heartbeats = {
        hb.job_name: hb
        for hb in JobHeartbeat.objects.filter(
            job_name__in=MONITORED_JOBS.keys(),
        )
    }

    jobs = {}
    all_healthy = True
    for job_name, max_age in MONITORED_JOBS.items():
        hb = heartbeats.get(job_name)
        if hb is None:
            jobs[job_name] = {
                'status': 'missing',
                'last_run_at': None,
                'age_seconds': None,
                'max_age_seconds': int(max_age.total_seconds()),
            }
            all_healthy = False
            continue

        age = now - hb.last_run_at
        is_stale = age > max_age
        is_errored = hb.last_status == JobHeartbeat.Status.ERROR
        healthy = not is_stale and not is_errored
        all_healthy = all_healthy and healthy

        if is_errored:
            job_status = 'errored'
        elif is_stale:
            job_status = 'stale'
        else:
            job_status = 'ok'

        jobs[job_name] = {
            'status': job_status,
            'last_run_at': hb.last_run_at.isoformat(),
            'last_status': hb.last_status,
            'age_seconds': int(age.total_seconds()),
            'max_age_seconds': int(max_age.total_seconds()),
        }

    return all_healthy, jobs
