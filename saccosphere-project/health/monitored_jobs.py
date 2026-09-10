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
    # config/celery.py: 'reconcile-savings-ledger', crontab 06:45 daily.
    'reconcile_savings_ledger': timedelta(days=2),
    # NOTE: 'accrue_savings_interest' (config/celery.py, monthly on the
    # 1st) deliberately writes a JobHeartbeat but is NOT registered here.
    # A once-a-month cadence would leave /health/jobs/ reporting it
    # "missing" for up to a month after deploy and generally does not
    # fit a freshness gate designed for daily sweeps. Observability for
    # it is the heartbeat row + the savings_interest_accrual_run metric
    # + the per-SACCO SAVINGS_INTEREST_ACCRUED audit rows.
}
