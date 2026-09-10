"""Registry of scheduled jobs whose freshness ``/health/jobs/`` enforces.

Each entry maps a ``JobHeartbeat.job_name`` to the maximum age its last
run may reach before the job is reported stale (HTTP 503 on the jobs
endpoint). Give the interval a full period of grace on top of the
schedule so a single slow or slightly delayed run does not page anyone.
"""

from datetime import timedelta


MONITORED_JOBS = {
    # config/celery.py: 'daily-npl-arrears-check', crontab 06:30 daily.
    # One missed day is tolerated; two is a real outage.
    'flag_npl_arrears': timedelta(days=2),
}
