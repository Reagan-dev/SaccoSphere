"""Signals for keeping guarantor capacity in sync with workflow changes."""

from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from services.engines.guarantor_logic import update_guarantee_capacity
from services.models import Guarantor, Loan


@receiver(pre_save, sender=Guarantor)
def track_previous_guarantor_status(sender, instance, **kwargs):
    """Track the previous guarantor status to detect status changes."""
    if not instance.pk:
        instance._previous_status = None
        return

    previous_status = sender.objects.filter(pk=instance.pk).values_list(
        'status',
        flat=True,
    ).first()
    instance._previous_status = previous_status


@receiver(post_save, sender=Guarantor)
def refresh_capacity_on_guarantor_status_change(
    sender,
    instance,
    created,
    **kwargs,
):
    """Refresh guarantor capacity when a guarantor responds."""
    if created:
        return

    previous_status = getattr(instance, '_previous_status', None)
    if (
        instance.status in [Guarantor.Status.APPROVED, Guarantor.Status.DECLINED]
        and previous_status != instance.status
    ):
        update_guarantee_capacity(instance.guarantor)


@receiver(pre_save, sender=Loan)
def track_previous_loan_status(sender, instance, **kwargs):
    """Track previous loan status to detect transition to completed."""
    if not instance.pk:
        instance._previous_status = None
        return

    previous_status = sender.objects.filter(pk=instance.pk).values_list(
        'status',
        flat=True,
    ).first()
    instance._previous_status = previous_status


@receiver(post_save, sender=Loan)
def refresh_capacity_on_loan_completion(sender, instance, created, **kwargs):
    """Restore guarantor capacity when a loan moves to completed."""
    if created:
        return

    previous_status = getattr(instance, '_previous_status', None)
    if (
        previous_status != Loan.Status.COMPLETED
        and instance.status == Loan.Status.COMPLETED
    ):
        approved_guarantors = instance.guarantors.filter(
            status=Guarantor.Status.APPROVED,
        ).select_related('guarantor')
        for guarantor in approved_guarantors:
            update_guarantee_capacity(guarantor.guarantor)
