"""NPL early-warning calculations for loan arrears."""

from django.utils import timezone

from services.models import NPLFlag, RepaymentSchedule


UNPAID_STATUSES = (
    RepaymentSchedule.Status.PENDING,
    RepaymentSchedule.Status.OVERDUE,
)


def get_arrears_bucket(loan):
    """Return the highest NPL threshold crossed by the earliest unpaid row."""
    earliest_unpaid = RepaymentSchedule.objects.filter(
        loan=loan,
        status__in=UNPAID_STATUSES,
    ).order_by('due_date', 'instalment_number').first()

    if earliest_unpaid is None:
        return None

    days_overdue = earliest_unpaid.days_overdue
    if days_overdue >= 90:
        return 90
    if days_overdue >= 60:
        return 60
    if days_overdue >= 30:
        return 30

    return None


def resolve_cleared_npl_flags(loan):
    """Resolve open NPL flags when the loan has fully caught up."""
    has_unpaid_schedules = RepaymentSchedule.objects.filter(
        loan=loan,
        status__in=UNPAID_STATUSES,
    ).exists()

    if has_unpaid_schedules:
        return 0

    return NPLFlag.objects.filter(
        loan=loan,
        resolved=False,
    ).update(
        resolved=True,
        resolved_at=timezone.now(),
    )
