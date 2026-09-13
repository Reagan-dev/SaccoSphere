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
    # config/settings/base.py: 'reconcile-stale-mpesa-transactions',
    # crontab every 5 minutes. Six missed cycles (30 min) is well past
    # what a single slow run explains and worth paging on immediately -
    # this sweep is the only automated path back to a resolved state for
    # M-Pesa transactions whose callback was lost or never arrived.
    'reconcile_stale_mpesa_transactions': timedelta(minutes=30),
    # NOTE: 'accrue_savings_interest' (config/celery.py, monthly on the
    # 1st) deliberately writes a JobHeartbeat but is NOT registered here.
    # A once-a-month cadence would leave /health/jobs/ reporting it
    # "missing" for up to a month after deploy and generally does not
    # fit a freshness gate designed for daily sweeps. Observability for
    # it is the heartbeat row + the savings_interest_accrual_run metric
    # + the per-SACCO SAVINGS_INTEREST_ACCRUED audit rows.
    # config/settings/base.py: 'check-overdue-invoices', crontab 08:00 daily.
    'update_overdue_invoices': timedelta(days=2),
    # config/settings/base.py: 'suspend-overdue-saccos', crontab 09:00 daily.
    'suspend_overdue_saccos': timedelta(days=2),
    # config/settings/base.py: 'send-billing-suspension-warnings',
    # crontab 08:30 daily - runs just before the suspension sweep above.
    'send_billing_suspension_warnings': timedelta(days=2),
    # NOTE: 'generate_monthly_invoices' (config/settings/base.py, monthly
    # on the 1st) deliberately writes a JobHeartbeat but is NOT registered
    # here, for the same reason as 'accrue_savings_interest' above -- a
    # monthly cadence does not fit this daily-sweep freshness gate.
    # Observability for it is the heartbeat row + the
    # billing_invoice_generated/billing_invoice_sent metrics + the
    # per-SACCO failure email from _notify_platform_admins_of_fee_report_
    # failures when any SACCO's generation fails.
}
