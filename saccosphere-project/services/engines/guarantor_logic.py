"""Guarantor capacity calculation and persistence helpers."""

from decimal import Decimal

from django.db.models import Sum

from services.models import GuaranteeCapacity, Guarantor, Loan, Saving


def calculate_guarantee_capacity(user):
    """
    Calculate a user's guarantee capacity across all their SACCOs.

    50% of total active savings can be guaranteed after deducting active
    guarantees tied to active loan statuses.
    """
    total_savings = (
        Saving.objects.filter(
            membership__user=user,
            status=Saving.Status.ACTIVE,
        ).aggregate(Sum('amount'))['amount__sum']
        or Decimal('0')
    )
    max_guarantee_capacity = total_savings * Decimal('0.50')
    active_guarantees = (
        Guarantor.objects.filter(
            guarantor=user,
            status=Guarantor.Status.APPROVED,
            loan__status__in=[
                Loan.Status.ACTIVE,
                Loan.Status.DISBURSEMENT_PENDING,
                Loan.Status.BOARD_REVIEW,
            ],
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
