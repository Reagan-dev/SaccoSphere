"""Cache invalidation for the member dashboard.

The dashboard views (dashboard/views.py) cache each member's portfolio,
onboarding state, and activity feed for a short TTL. That is fine for read
traffic, but a member who just made a deposit or had a loan instalment
posted should not have to wait out the TTL to see it reflected - so every
write that can change what those views return also clears the affected
cache keys here, instead of relying on time alone.

A failure here (a bad cache backend, an unexpected missing relation) must
never break the write that triggered it - a failed Transaction/Loan/Saving
save because Redis is briefly down would be far worse than one member
seeing a stale dashboard for a few seconds - so every receiver below is
wrapped end to end and only ever logs on failure.
"""

import logging

from django.core.cache import cache
from django.db.models.signals import post_save
from django.dispatch import receiver

from payments.models import Transaction
from saccomembership.models import Membership
from services.models import Loan, RepaymentSchedule, Saving


logger = logging.getLogger('saccosphere.dashboard')


def _invalidate(
    user_id, *, portfolio=False, dashboard_state=False, activity_feed=False,
):
    if user_id is None:
        return

    keys = []
    if portfolio:
        keys.append(f'portfolio:{user_id}')
    if dashboard_state:
        keys.append(f'dashboard_state:{user_id}')
    if activity_feed:
        keys.append(f'activity_feed:{user_id}')

    cache.delete_many(keys)


@receiver(post_save, sender=Transaction)
def invalidate_on_transaction_saved(sender, instance, **kwargs):
    try:
        _invalidate(instance.user_id, portfolio=True, activity_feed=True)
    except Exception:
        logger.exception(
            'Dashboard cache invalidation failed for Transaction %s.',
            instance.pk,
        )


@receiver(post_save, sender=Saving)
def invalidate_on_saving_saved(sender, instance, **kwargs):
    try:
        _invalidate(instance.membership.user_id, portfolio=True)
    except Exception:
        logger.exception(
            'Dashboard cache invalidation failed for Saving %s.',
            instance.pk,
        )


@receiver(post_save, sender=Loan)
def invalidate_on_loan_saved(sender, instance, **kwargs):
    try:
        _invalidate(instance.membership.user_id, portfolio=True)
    except Exception:
        logger.exception(
            'Dashboard cache invalidation failed for Loan %s.', instance.pk,
        )


@receiver(post_save, sender=RepaymentSchedule)
def invalidate_on_repayment_schedule_saved(sender, instance, **kwargs):
    try:
        _invalidate(
            instance.loan.membership.user_id,
            portfolio=True,
            activity_feed=True,
        )
    except Exception:
        logger.exception(
            'Dashboard cache invalidation failed for RepaymentSchedule %s.',
            instance.pk,
        )


@receiver(post_save, sender=Membership)
def invalidate_on_membership_saved(sender, instance, **kwargs):
    try:
        _invalidate(instance.user_id, portfolio=True, dashboard_state=True)
    except Exception:
        logger.exception(
            'Dashboard cache invalidation failed for Membership %s.',
            instance.pk,
        )
