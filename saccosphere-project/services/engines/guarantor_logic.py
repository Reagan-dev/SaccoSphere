"""Guarantor capacity calculation and persistence helpers."""

from decimal import Decimal

from django.db.models import Sum

from services.engines.loan_limits import OUTSTANDING_LOAN_STATUSES
from services.models import GuaranteeCapacity, Guarantor, Saving


def calculate_guarantee_capacity(user):
    """
    Calculate a user's guarantee capacity across all their SACCOs.

    50% of total ACTIVE savings (summed over every SACCO the user belongs
    to) may be guaranteed, minus the guarantee_amount of every APPROVED
    guarantee whose loan has not yet reached a terminal state
    (OUTSTANDING_LOAN_STATUSES). This is the single source of truth for
    guarantee capacity; the persisted GuaranteeCapacity row is a cache of
    it maintained by update_guarantee_capacity and the signals.
    """
    total_savings = (
        Saving.objects.filter(
            membership__user=user,
            status=Saving.Status.ACTIVE,
        ).aggregate(Sum('amount'))['amount__sum']
        or Decimal('0')
    )
    max_guarantee_capacity = total_savings * Decimal('0.50')
    # An APPROVED guarantee reserves capacity for the whole life of the
    # loan it backs - from application right through default - and is only
    # released when the loan reaches a terminal state (COMPLETED /
    # REJECTED). OUTSTANDING_LOAN_STATUSES is exactly that set, shared
    # with the loan-limit engine.
    active_guarantees = (
        Guarantor.objects.filter(
            guarantor=user,
            status=Guarantor.Status.APPROVED,
            loan__status__in=OUTSTANDING_LOAN_STATUSES,
        ).aggregate(Sum('guarantee_amount'))['guarantee_amount__sum']
        or Decimal('0')
    )
    available_capacity = max(
        max_guarantee_capacity - active_guarantees,
        Decimal('0'),
    )

    return {
        'total_savings': total_savings,
        'max_guarantee_capacity': max_guarantee_capacity,
        'active_guarantees': active_guarantees,
        'available_capacity': available_capacity,
    }


def update_guarantee_capacity(user):
    """Recalculate and persist the user's guarantee capacity snapshot."""
    data = calculate_guarantee_capacity(user)
    capacity, _ = GuaranteeCapacity.objects.update_or_create(
        user=user,
        defaults={
            'total_savings': data['total_savings'],
            'active_guarantees': data['active_guarantees'],
            'available_capacity': data['available_capacity'],
        },
    )
    return capacity


def lock_guarantee_capacity(user):
    """Return the user's GuaranteeCapacity row locked FOR UPDATE.

    Must be called inside a transaction. Ensures the row exists, then
    locks it so that concurrent guarantee approvals for the same
    guarantor serialise on this one row: the second approval blocks here
    until the first commits, then re-derives capacity with
    calculate_guarantee_capacity (which now sees the first guarantee as
    APPROVED) and is rejected if the two would jointly over-commit.
    """
    GuaranteeCapacity.objects.get_or_create(user=user)
    return GuaranteeCapacity.objects.select_for_update().get(user=user)
