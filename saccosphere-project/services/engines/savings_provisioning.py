"""The single savings-account creation path.

Every way a ``Saving`` row can come into existence - the admin API view,
a future membership-approval signal, an admin action, a backfill command
- must call :func:`open_savings_account` instead of touching ``Saving``
directly, so the tenant checks, the one-account-per-type rule and the
opening-deposit ledger entry live in exactly one place.
"""

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.core.exceptions import ValidationError
from django.db import transaction as db_transaction

from ledger.models import LedgerEntry
from ledger.utils import apply_ledger_entry
from saccomembership.models import Membership
from services.models import Saving


MONEY_QUANTIZER = Decimal('0.01')
ZERO = Decimal('0.00')


class SavingsAccountError(ValidationError):
    """A savings account could not be opened as requested.

    Subclasses ``ValidationError`` so DRF/admin surface it as a 400 by
    default; the API view maps it to 409 for the "already exists" case.
    """


def open_savings_account(membership, savings_type, opening_balance=None):
    """Open one savings account for ``membership`` under ``savings_type``.

    Validates that the membership and the savings type belong to the same
    SACCO and that the one-account-per-(membership, type) rule is honoured
    - unless the type sets ``allows_multiple_accounts`` (Prompt 14). When
    ``opening_balance`` is a positive amount it is posted through
    :func:`ledger.utils.apply_ledger_entry` as a real ``SAVING_DEPOSIT``
    ledger entry (a genuine member contribution recorded at opening),
    never a bare ``Saving.amount`` assignment.

    Returns the created ``Saving``. Raises :class:`SavingsAccountError`
    on any rule violation; callers translate it to the right HTTP status.

    Concurrency: the member row is locked ``FOR UPDATE`` for the duration,
    so two racing opens for the same member serialise and the second sees
    the first account (there is no DB unique constraint after Prompt 14).
    """
    if savings_type.sacco_id is None:
        raise SavingsAccountError('The savings type is not tied to a SACCO.')
    if membership.sacco_id != savings_type.sacco_id:
        raise SavingsAccountError(
            'The membership and the savings type belong to different '
            'SACCOs.'
        )

    amount = _clean_opening_balance(opening_balance)
    allows_multiple = bool(
        getattr(savings_type, 'allows_multiple_accounts', False)
    )

    with db_transaction.atomic():
        # Serialise concurrent open-account calls for this member.
        Membership.objects.select_for_update().get(pk=membership.pk)

        already = Saving.objects.filter(
            membership=membership,
            savings_type=savings_type,
        ).exists()
        if already and not allows_multiple:
            raise SavingsAccountError(
                f'This member already has a {savings_type.name} savings '
                'account and this type does not allow multiple accounts.'
            )

        saving = Saving(
            membership=membership,
            savings_type=savings_type,
            amount=ZERO,
            status=Saving.Status.ACTIVE,
        )
        # Runs Saving.clean() too, so any future model-level rule is
        # enforced through this path as well.
        saving.full_clean()
        saving.save()

        if amount > ZERO:
            apply_ledger_entry(
                saving=saving,
                amount=amount,
                entry_type=LedgerEntry.EntryType.CREDIT,
                category=LedgerEntry.Category.SAVING_DEPOSIT,
                description=(
                    f'Opening deposit for {savings_type.name} savings '
                    'account.'
                ),
                reference=f'SAV-OPEN-{saving.id}',
                contribution_delta=amount,
            )

    return saving


def _clean_opening_balance(opening_balance):
    """Normalise the optional opening balance to a non-negative Decimal."""
    if opening_balance is None:
        return ZERO
    try:
        amount = Decimal(str(opening_balance)).quantize(
            MONEY_QUANTIZER,
            rounding=ROUND_HALF_UP,
        )
    except (InvalidOperation, TypeError, ValueError):
        raise SavingsAccountError('Opening balance is not a valid amount.')
    if amount < ZERO:
        raise SavingsAccountError('Opening balance cannot be negative.')
    return amount
