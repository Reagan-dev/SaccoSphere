"""Loan eligibility and limit calculations."""

from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from saccomembership.models import Membership
from services.models import Loan, Saving


ZERO = Decimal('0')

# Loan statuses where the member still owes, or is committed to owe, money.
# Everything except COMPLETED (repaid) and REJECTED (never advanced). A
# loan anywhere in the approval pipeline already reserves its requested
# amount (LoanApplySerializer.create sets outstanding_balance=amount), so
# two concurrent applications can't each be sized against a limit that
# ignores the other.
OUTSTANDING_LOAN_STATUSES = (
    Loan.Status.PENDING,
    Loan.Status.GUARANTORS_PENDING,
    Loan.Status.PENDING_APPROVAL,
    Loan.Status.UNDER_REVIEW,
    Loan.Status.BOARD_REVIEW,
    Loan.Status.APPROVED,
    Loan.Status.DISBURSED,
    Loan.Status.DISBURSEMENT_PENDING,
    Loan.Status.ACTIVE,
    Loan.Status.DEFAULTED,
)


def _get_loan_multiplier(sacco):
    """Return loan multiplier from SaccoSettings or the SACCO default."""
    settings = getattr(sacco, 'settings', None)
    if settings is not None:
        return Decimal(settings.loan_multiplier)
    return Decimal(sacco.loan_multiplier)


def lock_member_loan_capacity_rows(membership):
    """Lock the savings + outstanding-loan rows calculate_loan_limit reads.

    Must be called inside a transaction, immediately before
    calculate_loan_limit, so two concurrent loan applications from the
    same member serialise: the second request blocks here until the first
    commits its new loan row, then re-reads a limit that already accounts
    for it instead of a stale snapshot.

    Lock order is savings first, then loans, each ordered by pk. Any code
    that locks both row types for a member must use this same order to
    avoid deadlocks.
    """
    list(
        Saving.objects.select_for_update()
        .filter(membership=membership, status=Saving.Status.ACTIVE)
        .order_by('pk')
    )
    list(
        Loan.objects.select_for_update()
        .filter(
            membership=membership,
            status__in=OUTSTANDING_LOAN_STATUSES,
        )
        .order_by('pk')
    )


def calculate_loan_limit(user, sacco):
    """
    Calculate how much a member can borrow from a specific SACCO.

    Returns a dict with eligibility details.
    """
    membership = (
        Membership.objects.select_related('user', 'sacco')
        .filter(
            user=user,
            sacco=sacco,
            status=Membership.Status.APPROVED,
        )
        .first()
    )

    if membership is None:
        return {
            'eligible': False,
            'reason': 'NOT_A_MEMBER',
            'max_amount': ZERO,
        }

    today = timezone.localdate()
    approved_date = membership.approved_date
    approved_local_date = (
        timezone.localtime(approved_date).date()
        if approved_date is not None
        else today
    )
    months_active = (today - approved_local_date).days // 30

    if months_active < sacco.min_loan_months:
        return {
            'eligible': False,
            'reason': 'MEMBERSHIP_TOO_NEW',
            'max_amount': ZERO,
            'months_active': months_active,
            'months_required': sacco.min_loan_months,
        }

    savings_query = Saving.objects.select_related(
        'membership',
        'membership__sacco',
        'savings_type',
    ).filter(
        membership=membership,
        status=Saving.Status.ACTIVE,
    )
    total_savings = savings_query.aggregate(
        total=Sum('amount'),
    )['total'] or ZERO

    if total_savings == ZERO:
        return {
            'eligible': False,
            'reason': 'NO_SAVINGS',
            'max_amount': ZERO,
        }

    gross_limit = total_savings * _get_loan_multiplier(sacco)

    outstanding_loans = Loan.objects.select_related(
        'membership',
        'membership__sacco',
        'loan_type',
    ).filter(
        membership=membership,
        status__in=OUTSTANDING_LOAN_STATUSES,
    )
    existing_balance = outstanding_loans.aggregate(
        total=Sum('outstanding_balance'),
    )['total'] or ZERO
    net_limit = max(gross_limit - existing_balance, ZERO)

    sacco_settings = getattr(sacco, 'settings', None)
    if sacco_settings is not None and sacco_settings.max_loan_amount is not None:
        net_limit = min(net_limit, sacco_settings.max_loan_amount)

    has_default = Loan.objects.select_related(
        'membership',
        'membership__sacco',
        'loan_type',
    ).filter(
        membership=membership,
        status=Loan.Status.DEFAULTED,
    ).exists()

    if has_default:
        return {
            'eligible': False,
            'reason': 'HAS_DEFAULT',
            'max_amount': ZERO,
        }

    guarantors_required = 0
    if net_limit > Decimal('200000'):
        guarantors_required = 2
    elif net_limit > Decimal('50000'):
        guarantors_required = 1

    return {
        'eligible': True,
        'max_amount': net_limit,
        'total_savings': total_savings,
        'existing_balance': existing_balance,
        'months_active': months_active,
        'guarantors_required': guarantors_required,
        'reason': None,
    }

