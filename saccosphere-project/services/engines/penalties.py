"""Per-SACCO late-repayment penalty computation.

Every SACCO configures its own rule on SaccoSettings
(penalty_type / penalty_rate / penalty_grace_days). This module is the
single place that turns that rule into a KES figure for one overdue
instalment; the overdue sweep (services.tasks.mark_overdue_instalments)
writes the result to RepaymentSchedule.penalty_amount, which the overdue
reminder then reads.
"""

from decimal import ROUND_HALF_UP, Decimal

from django.utils import timezone


ZERO = Decimal('0.00')
_QUANT = Decimal('0.01')


def compute_penalty(schedule_item, sacco_settings, as_of=None):
    """KES penalty owed on one instalment under the SACCO's rule.

    Returns Decimal quantised to cents. Zero when: there are no SACCO
    settings, the rule is NONE, the instalment is still within the grace
    window, or the instalment is already fully paid.

    ``as_of`` (a date) lets callers/tests pin "today".
    """
    if sacco_settings is None:
        return ZERO

    penalty_type = sacco_settings.penalty_type
    none_type = type(sacco_settings).PenaltyType.NONE
    if penalty_type == none_type:
        return ZERO

    as_of = as_of or timezone.localdate()
    days_overdue = (as_of - schedule_item.due_date).days
    effective_days = days_overdue - sacco_settings.penalty_grace_days
    if effective_days <= 0:
        return ZERO

    paid = schedule_item.paid_amount or ZERO
    if schedule_item.amount - paid <= ZERO:
        return ZERO

    rate = sacco_settings.penalty_rate
    choices = type(sacco_settings).PenaltyType
    if penalty_type == choices.FLAT:
        penalty = rate
    elif penalty_type == choices.PERCENT_ONCE:
        penalty = schedule_item.amount * rate
    elif penalty_type == choices.PERCENT_PER_DAY:
        penalty = schedule_item.amount * rate * Decimal(effective_days)
    else:
        return ZERO

    return max(ZERO, penalty).quantize(_QUANT, rounding=ROUND_HALF_UP)
