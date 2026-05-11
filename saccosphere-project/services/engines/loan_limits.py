"""Loan eligibility and limit calculations."""

from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from saccomembership.models import Membership
from services.models import Loan, Saving


ZERO = Decimal('0')


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

    gross_limit = total_savings * Decimal(sacco.loan_multiplier)

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


# ============================================================
# REVIEW - READ THIS THEN DELETE FROM THIS LINE TO THE END
# ============================================================
#
# services/engines/loan_limits.py
#
# calculate_loan_limit checks whether a user can borrow from one SACCO and
# returns the reason plus the maximum allowed amount. It first confirms the
# user is an approved member, checks how long they have belonged to the SACCO,
# totals their active savings, multiplies savings by the SACCO loan multiplier,
# subtracts active loan balances, blocks members with defaulted loans, and
# decides how many guarantors are required.
#
# services/views.py
#
# LoanEligibilityView exposes the calculation through the API. It reads
# sacco_id from the query string, finds that SACCO, caches the result for five
# minutes, and returns the eligibility details to the authenticated user.
#
# LoanApplyView.create now checks the same eligibility before saving a new loan.
# This prevents members from applying when they are too new, have no savings,
# have defaulted loans, or request more than their allowed limit.
#
# services/management/commands/seed_loan_types.py
#
# seed_loan_types creates the standard loan products for each active SACCO only
# when that SACCO does not already have loan types. This avoids duplicating or
# overwriting products a SACCO has already configured.
#
# Django/Python concepts you might not know well
#
# select_related tells Django to fetch related ForeignKey objects in the same
# database query. It is useful when code needs fields from linked objects like
# membership.sacco.
#
# aggregate(Sum('amount')) asks the database to calculate a total. This is
# faster and safer than loading every row into Python and adding them manually.
#
# Decimal is used for money because normal Python floats can introduce tiny
# rounding errors.
#
# Django cache stores a value for a short period. Here it keeps eligibility
# results for 300 seconds so repeated API calls do not hit the database every
# time.
#
# One manual test
#
# Log in as a member with approved membership, create active savings for that
# member, then call GET /api/v1/services/loans/eligibility/?sacco_id=<sacco_id>.
# Confirm the max_amount is savings multiplied by the SACCO loan multiplier.
#
# Important design decision
#
# The limit calculation lives in services/engines/loan_limits.py instead of the
# view. That keeps the rule reusable by both the eligibility endpoint and the
# loan application endpoint, so both places enforce the same business logic.
#
# END OF REVIEW - DELETE EVERYTHING FROM THE FIRST # LINE ABOVE
# ============================================================
