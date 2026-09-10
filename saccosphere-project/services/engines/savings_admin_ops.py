"""Sanctioned manual savings operations for staff (Django admin today,
any future status-change / correction API tomorrow).

A staff member must never rewrite ``Saving.amount`` or ``Saving.status``
with a bare model-field save (no ledger row, no audit trail). These two
helpers are the only supported way:

* :func:`create_savings_adjustment` moves the balance *through*
  ``ledger.utils.apply_ledger_entry`` (Prompt 7) so a ledger row always
  exists, and writes a ``log_audit`` row with before/after and the
  reason.
* :func:`set_saving_status` flips ACTIVE / FROZEN / CLOSED under a row
  lock and writes a ``log_audit`` row.

Category choice: a manual correction is booked as ``SAVING_DEPOSIT`` /
``SAVING_WITHDRAWAL`` (not the generic ``ADJUSTMENT`` category) so it
stays inside ``SAVINGS_LEDGER_CATEGORIES`` and the daily
``reconcile_savings_ledger`` task keeps matching ``Saving.amount``. The
member's ``total_contributions`` / ``total_withdrawals`` are deliberately
NOT moved - an admin correction is not a member contribution or a
withdrawal.
"""

from decimal import Decimal, ROUND_HALF_UP
from uuid import uuid4

from django.db import transaction as db_transaction

from ledger.models import LedgerEntry
from ledger.utils import apply_ledger_entry
from saccomanagement.audit_logger import log_audit
from services.models import Saving


MONEY_QUANTIZER = Decimal('0.01')
ZERO = Decimal('0.00')
MIN_REASON_LENGTH = 10

CREDIT = 'CREDIT'
DEBIT = 'DEBIT'

_CATEGORY = {
    CREDIT: LedgerEntry.Category.SAVING_DEPOSIT,
    DEBIT: LedgerEntry.Category.SAVING_WITHDRAWAL,
}
_ENTRY_TYPE = {
    CREDIT: LedgerEntry.EntryType.CREDIT,
    DEBIT: LedgerEntry.EntryType.DEBIT,
}
_STATUSES = {
    Saving.Status.ACTIVE,
    Saving.Status.FROZEN,
    Saving.Status.CLOSED,
}


class SavingsAdminOpError(ValueError):
    """A manual savings operation was rejected."""


def create_savings_adjustment(saving, *, amount, direction, actor, reason):
    """Move one savings balance by ``amount`` and record it fully.

    ``direction`` is ``'CREDIT'`` (increase) or ``'DEBIT'`` (decrease).
    Returns the created :class:`~ledger.models.LedgerEntry`. Raises
    :class:`SavingsAdminOpError` on any validation failure.
    """
    reason = (reason or '').strip()
    if len(reason) < MIN_REASON_LENGTH:
        raise SavingsAdminOpError(
            f'A reason of at least {MIN_REASON_LENGTH} characters is '
            'required for a manual balance adjustment.'
        )
    if direction not in (CREDIT, DEBIT):
        raise SavingsAdminOpError("direction must be 'CREDIT' or 'DEBIT'.")

    try:
        amount = Decimal(str(amount)).quantize(
            MONEY_QUANTIZER, rounding=ROUND_HALF_UP,
        )
    except (ArithmeticError, TypeError, ValueError):
        raise SavingsAdminOpError('Adjustment amount is not a valid number.')
    if amount <= ZERO:
        raise SavingsAdminOpError('Adjustment amount must be positive.')

    with db_transaction.atomic():
        locked = (
            Saving.objects.select_for_update()
            .select_related('membership')
            .get(pk=saving.pk)
        )
        before = locked.amount
        if direction == DEBIT and amount > before:
            raise SavingsAdminOpError(
                'A debit adjustment cannot exceed the current balance '
                f'({before}).'
            )

        entry = apply_ledger_entry(
            saving=locked,
            amount=amount,
            entry_type=_ENTRY_TYPE[direction],
            category=_CATEGORY[direction],
            description=(f'[ADMIN ADJUSTMENT] {reason}')[:255],
            reference=f'ADJ-{uuid4().hex}',
        )
        after = locked.amount

    log_audit(
        actor,
        'SAVING_BALANCE_ADJUSTED',
        'Saving',
        saving.pk,
        old_values={'amount': str(before)},
        new_values={
            'amount': str(after),
            'direction': direction,
            'adjustment_amount': str(amount),
            'reason': reason,
            'ledger_entry_reference': entry.reference,
        },
    )

    # Reflect the committed balance on the caller's instance.
    saving.amount = after
    return entry


def set_saving_status(saving, *, new_status, actor, reason=None, request=None):
    """Change a savings account's status under a row lock, with an audit
    row. The single write+audit primitive for freeze / close / reactivate.

    Callers that need *legal-transition* enforcement and the member
    notification (the API) go through :func:`apply_savings_status_action`;
    the Django-admin action calls this directly (super-admin, permissive).

    Returns the locked ``Saving``. A no-op transition is allowed and
    simply logs nothing.
    """
    if new_status not in _STATUSES:
        raise SavingsAdminOpError(f'Unknown savings status {new_status!r}.')
    reason = (reason or '').strip()

    with db_transaction.atomic():
        locked = Saving.objects.select_for_update().get(pk=saving.pk)
        old_status = locked.status
        if old_status == new_status:
            return locked
        locked.status = new_status
        locked.save(update_fields=['status', 'updated_at'])

    log_audit(
        actor,
        'SAVINGS_STATUS_CHANGED',
        'Saving',
        saving.pk,
        old_values={'status': old_status},
        new_values={'status': new_status, 'reason': reason},
        request=request,
    )

    saving.status = new_status
    return locked


# Legal savings-account status transitions. CLOSED is TERMINAL - a closed
# account is never reopened (a member who returns opens a new one). This
# keeps a wound-down account's state permanently settled for audit and
# stops an accidental "reactivate" from resurrecting money that was
# deliberately closed out.
_ACTION_TARGET = {
    'freeze': Saving.Status.FROZEN,
    'close': Saving.Status.CLOSED,
    'reactivate': Saving.Status.ACTIVE,
}
_LEGAL_TRANSITIONS = {
    Saving.Status.ACTIVE: {Saving.Status.FROZEN, Saving.Status.CLOSED},
    Saving.Status.FROZEN: {Saving.Status.ACTIVE, Saving.Status.CLOSED},
    Saving.Status.CLOSED: set(),
}
STATUS_ACTIONS = tuple(_ACTION_TARGET)


def apply_savings_status_action(
    saving, *, action, actor, reason, request=None,
):
    """API-facing freeze / close / reactivate.

    Locks the row, checks the transition is legal *from the current
    status*, writes + audits via :func:`set_saving_status`, and notifies
    the affected member on freeze/close (it touches their money).

    Raises :class:`SavingsAdminOpError` for an unknown action, an illegal
    or no-op transition, or a too-short reason. Returns the locked
    ``Saving``.
    """
    reason = (reason or '').strip()
    if len(reason) < MIN_REASON_LENGTH:
        raise SavingsAdminOpError(
            f'A reason of at least {MIN_REASON_LENGTH} characters is '
            'required to change an account status.'
        )
    target = _ACTION_TARGET.get(action)
    if target is None:
        raise SavingsAdminOpError(
            "action must be one of 'freeze', 'close', 'reactivate'."
        )

    with db_transaction.atomic():
        locked = (
            Saving.objects.select_for_update()
            .select_related(
                'membership',
                'membership__user',
                'membership__sacco',
                'savings_type',
            )
            .get(pk=saving.pk)
        )
        current = locked.status
        if current == target:
            raise SavingsAdminOpError(
                f'This account is already {current.lower()}.'
            )
        if target not in _LEGAL_TRANSITIONS.get(current, set()):
            raise SavingsAdminOpError(
                f'Cannot {action} a {current.lower()} savings account.'
            )

        set_saving_status(
            locked,
            new_status=target,
            actor=actor,
            reason=reason,
            request=request,
        )

        if target in (Saving.Status.FROZEN, Saving.Status.CLOSED):
            _notify_member_of_status_change(locked, target, reason)

    saving.status = target
    return locked


def _notify_member_of_status_change(saving, new_status, reason):
    """Best-effort in-app notice to the member on freeze/close.

    ``create_notification`` is itself crash-safe (it swallows and logs
    its own errors), so a notification-infra hiccup can never roll back
    the status change.
    """
    from notifications.models import Notification
    from notifications.utils import create_notification

    verb = 'frozen' if new_status == Saving.Status.FROZEN else 'closed'
    product = (
        saving.savings_type.name if saving.savings_type_id else 'savings'
    )
    create_notification(
        user=saving.membership.user,
        title=f'Your {product} account has been {verb}',
        message=(
            f'Your savings account at {saving.membership.sacco.name} has '
            f'been {verb} by an administrator. Reason: {reason}. Please '
            'contact your SACCO for details.'
        ),
        category=Notification.Category.ALERT,
        related_object_type='Saving',
        related_object_id=str(saving.id),
        dispatch_async=False,
    )


def set_dividend_eligibility(saving, *, eligible, actor, reason, request=None):
    """Toggle ``Saving.dividend_eligible`` under a row lock, with an audit
    row (``DIVIDEND_ELIGIBILITY_CHANGED``). Rejects a too-short reason and
    a no-op change. Returns the locked ``Saving``.
    """
    reason = (reason or '').strip()
    if len(reason) < MIN_REASON_LENGTH:
        raise SavingsAdminOpError(
            f'A reason of at least {MIN_REASON_LENGTH} characters is '
            'required to change dividend eligibility.'
        )
    eligible = bool(eligible)

    with db_transaction.atomic():
        locked = Saving.objects.select_for_update().get(pk=saving.pk)
        before = locked.dividend_eligible
        if before == eligible:
            state = 'enabled' if eligible else 'disabled'
            raise SavingsAdminOpError(
                f'Dividend eligibility is already {state}.'
            )
        locked.dividend_eligible = eligible
        locked.save(update_fields=['dividend_eligible', 'updated_at'])

    log_audit(
        actor,
        'DIVIDEND_ELIGIBILITY_CHANGED',
        'Saving',
        saving.pk,
        old_values={'dividend_eligible': before},
        new_values={'dividend_eligible': eligible, 'reason': reason},
        request=request,
    )

    saving.dividend_eligible = eligible
    return locked
