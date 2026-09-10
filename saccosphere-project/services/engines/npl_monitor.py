"""NPL early-warning calculations for loan arrears.

The batch helpers here exist so the daily sweep
(:func:`services.tasks.flag_npl_arrears`) can compute arrears for the
whole portfolio in a fixed number of queries instead of one-per-loan.
The single-loan helpers are kept for callers that genuinely work on one
loan at a time (tests, ad-hoc checks).
"""

from django.db.models import Min
from django.utils import timezone

from services.models import Loan, NPLFlag, RepaymentSchedule


UNPAID_STATUSES = (
    RepaymentSchedule.Status.PENDING,
    RepaymentSchedule.Status.OVERDUE,
)

# Loan statuses the arrears sweep cares about.
MONITORED_LOAN_STATUSES = (
    Loan.Status.ACTIVE,
    Loan.Status.DEFAULTED,
)


def bucket_for_days(days_overdue):
    """Map a days-overdue count to the highest NPL threshold it crosses."""
    if days_overdue >= 90:
        return 90
    if days_overdue >= 60:
        return 60
    if days_overdue >= 30:
        return 30
    return None


def get_arrears_bucket(loan):
    """Return the NPL threshold crossed by ``loan``'s earliest past-due row.

    Only instalments whose ``due_date`` has actually passed count - a
    future not-yet-due instalment is also ``PENDING`` but is not arrears.
    """
    earliest_unpaid = RepaymentSchedule.objects.filter(
        loan=loan,
        status__in=UNPAID_STATUSES,
        due_date__lte=timezone.localdate(),
    ).order_by('due_date', 'instalment_number').first()

    if earliest_unpaid is None:
        return None

    return bucket_for_days(earliest_unpaid.days_overdue)


def arrears_buckets_for_monitored_loans():
    """Map ``loan_id -> arrears bucket`` for every delinquent monitored loan.

    One aggregate query over past-due unpaid instalments, joined to the
    monitored loan statuses. Loans with no past-due unpaid instalment are
    simply absent from the result, so its size tracks actual delinquency,
    not portfolio size.
    """
    today = timezone.localdate()
    rows = (
        RepaymentSchedule.objects.filter(
            status__in=UNPAID_STATUSES,
            due_date__lte=today,
            loan__status__in=MONITORED_LOAN_STATUSES,
        )
        .values('loan_id')
        .annotate(earliest_due=Min('due_date'))
    )

    buckets = {}
    for row in rows:
        days_overdue = (today - row['earliest_due']).days
        bucket = bucket_for_days(days_overdue)
        if bucket is not None:
            buckets[row['loan_id']] = bucket
    return buckets


def has_past_due_instalment(loan):
    """True when ``loan`` has at least one unpaid instalment now past due."""
    return RepaymentSchedule.objects.filter(
        loan=loan,
        status__in=UNPAID_STATUSES,
        due_date__lte=timezone.localdate(),
    ).exists()


def resolve_cleared_npl_flags(loan):
    """Resolve open NPL flags once the loan has no *past-due* arrears left.

    Future not-yet-due instalments are also ``PENDING`` but must not keep
    a flag open - otherwise a multi-instalment loan that catches up on
    arrears stays flagged until final payoff.
    """
    if has_past_due_instalment(loan):
        return 0

    return NPLFlag.objects.filter(
        loan=loan,
        resolved=False,
    ).update(
        resolved=True,
        resolved_at=timezone.now(),
    )
