"""Loan eligibility and limit calculations."""

from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from saccomembership.models import Membership
from services.models import Loan, Saving


ZERO = Decimal('0')


def _get_loan_multiplier(sacco):
    """Return loan multiplier from SaccoSettings or the SACCO default."""
    settings = getattr(sacco, 'settings', None)
    if settings is not None:
        return Decimal(settings.loan_multiplier)
    return Decimal(sacco.loan_multiplier)


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

    active_loans = Loan.objects.select_related(
        'membership',
        'membership__sacco',
        'loan_type',
    ).filter(
        membership=membership,
        status=Loan.Status.ACTIVE,
    )
    existing_balance = active_loans.aggregate(
        total=Sum('outstanding_balance'),
    )['total'] or ZERO
    net_limit = max(gross_limit - existing_balance, ZERO)

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

