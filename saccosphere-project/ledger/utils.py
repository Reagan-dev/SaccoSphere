from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction as db_transaction
from django.db.models import Sum
from django.utils import timezone

from .engines.balance_calculator import (
    generate_reference,
)
from .models import LedgerEntry


MONEY_QUANTIZER = Decimal('0.01')
ZERO = Decimal('0.00')


CATEGORY_PREFIXES = {
    LedgerEntry.Category.SAVING_DEPOSIT: 'SAV',
    LedgerEntry.Category.SAVING_WITHDRAWAL: 'SAV',
    LedgerEntry.Category.LOAN_DISBURSEMENT: 'LOAN',
    LedgerEntry.Category.LOAN_REPAYMENT: 'REP',
    LedgerEntry.Category.FEE: 'FEE',
    LedgerEntry.Category.PENALTY: 'FEE',
    LedgerEntry.Category.DIVIDEND: 'SAV',
    LedgerEntry.Category.DIVIDEND_PAYOUT: 'DIV',
    LedgerEntry.Category.ADJUSTMENT: 'ADJ',
    LedgerEntry.Category.OPENING_BALANCE: 'OPEN',
    LedgerEntry.Category.SAVINGS_INTEREST: 'INT',
}


# The ledger categories that make up a member's savings balance. Loan,
# fee and generic ADJUSTMENT rows are deliberately excluded: LedgerEntry
# is membership-scoped with no per-savings-account link, so this is the
# whole-membership savings figure that ``sum(Saving.amount)`` must equal.
# The withdrawal reversal is a CREDIT ``SAVING_WITHDRAWAL`` (not an
# ADJUSTMENT) precisely so it lands in this set and nets correctly.
SAVINGS_LEDGER_CATEGORIES = (
    LedgerEntry.Category.SAVING_DEPOSIT,
    LedgerEntry.Category.SAVING_WITHDRAWAL,
    LedgerEntry.Category.DIVIDEND_PAYOUT,
    LedgerEntry.Category.OPENING_BALANCE,
    LedgerEntry.Category.SAVINGS_INTEREST,
)


def create_ledger_entry(
    membership,
    entry_type,
    category,
    amount,
    description,
    reference=None,
    transaction=None,
):
    """
    Create a ledger entry with its running balance.

    Low-level primitive. To change a savings balance use
    :func:`apply_ledger_entry` instead - it is the only path allowed to
    write ``Saving.amount``. The membership's existing ledger rows are
    locked while the new running balance is computed and written, so
    concurrent writes for the same membership are serialized.
    """
    amount = Decimal(str(amount)).quantize(
        MONEY_QUANTIZER,
        rounding=ROUND_HALF_UP,
    )

    with db_transaction.atomic():
        if reference is None:
            reference = generate_reference(_get_reference_prefix(category))

        locked_entries = list(
            LedgerEntry.objects.select_for_update()
            .filter(membership=membership)
            .only('entry_type', 'amount')
        )
        credits = sum(
            (
                entry.amount
                for entry in locked_entries
                if entry.entry_type == LedgerEntry.EntryType.CREDIT
            ),
            ZERO,
        )
        debits = sum(
            (
                entry.amount
                for entry in locked_entries
                if entry.entry_type == LedgerEntry.EntryType.DEBIT
            ),
            ZERO,
        )
        balance_before = credits - debits

        if entry_type == LedgerEntry.EntryType.CREDIT:
            balance_after = balance_before + amount
        else:
            balance_after = balance_before - amount

        return LedgerEntry.objects.create(
            membership=membership,
            entry_type=entry_type,
            category=category,
            amount=amount,
            reference=reference,
            description=description,
            balance_after=balance_after,
            transaction=transaction,
        )


def apply_ledger_entry(
    *,
    saving,
    amount,
    entry_type,
    category,
    description,
    reference=None,
    transaction=None,
    contribution_delta=None,
    withdrawal_delta=None,
    as_of=None,
):
    """The ONLY code path permitted to change ``Saving.amount``.

    In one atomic block: locks the ``Saving`` row ``FOR UPDATE``, appends
    the ``LedgerEntry`` via :func:`create_ledger_entry`, then moves
    ``Saving.amount`` by this entry's signed value (CREDIT ``+amount``,
    DEBIT ``-amount``). Optionally moves the informational
    ``total_contributions`` / ``total_withdrawals`` running totals by the
    given signed deltas, and stamps ``last_transaction_date``. Mutates the
    passed ``saving`` instance in place so the caller sees the new
    balance, and returns the created ``LedgerEntry``.

    Lock order is always Saving first, then LedgerEntry rows - every
    savings write path must follow it.

    Never call ``Saving.save(update_fields=['amount', ...])`` anywhere
    else.
    """
    from services.models import Saving

    amount = Decimal(str(amount)).quantize(
        MONEY_QUANTIZER,
        rounding=ROUND_HALF_UP,
    )
    if amount <= ZERO:
        raise ValueError('apply_ledger_entry() amount must be positive.')

    with db_transaction.atomic():
        locked = (
            Saving.objects.select_for_update()
            .select_related('membership')
            .get(pk=saving.pk)
        )

        entry = create_ledger_entry(
            membership=locked.membership,
            entry_type=entry_type,
            category=category,
            amount=amount,
            description=description,
            reference=reference,
            transaction=transaction,
        )

        signed = (
            amount
            if entry_type == LedgerEntry.EntryType.CREDIT
            else -amount
        )
        locked.amount = (locked.amount + signed).quantize(
            MONEY_QUANTIZER,
            rounding=ROUND_HALF_UP,
        )
        locked.last_transaction_date = as_of or timezone.localdate()
        update_fields = ['amount', 'last_transaction_date', 'updated_at']

        if contribution_delta is not None:
            locked.total_contributions = (
                locked.total_contributions
                + Decimal(str(contribution_delta))
            ).quantize(MONEY_QUANTIZER, rounding=ROUND_HALF_UP)
            update_fields.append('total_contributions')
        if withdrawal_delta is not None:
            locked.total_withdrawals = (
                locked.total_withdrawals
                + Decimal(str(withdrawal_delta))
            ).quantize(MONEY_QUANTIZER, rounding=ROUND_HALF_UP)
            update_fields.append('total_withdrawals')

        locked.save(update_fields=update_fields)

        # Reflect the committed values on the caller's instance.
        saving.amount = locked.amount
        saving.last_transaction_date = locked.last_transaction_date
        saving.total_contributions = locked.total_contributions
        saving.total_withdrawals = locked.total_withdrawals

        return entry


def savings_ledger_balance(membership):
    """Signed sum of a membership's savings-category ledger rows.

    The ledger's view of what the member's savings accounts hold in
    total (see :data:`SAVINGS_LEDGER_CATEGORIES`). Membership-scoped
    because ``LedgerEntry`` has no per-savings-account attribution.
    """
    rows = LedgerEntry.objects.filter(
        membership=membership,
        category__in=SAVINGS_LEDGER_CATEGORIES,
    )
    credits = rows.filter(
        entry_type=LedgerEntry.EntryType.CREDIT,
    ).aggregate(total=Sum('amount'))['total'] or ZERO
    debits = rows.filter(
        entry_type=LedgerEntry.EntryType.DEBIT,
    ).aggregate(total=Sum('amount'))['total'] or ZERO
    return (credits - debits).quantize(
        MONEY_QUANTIZER,
        rounding=ROUND_HALF_UP,
    )


def expected_savings_balance(membership):
    """Sum of ``Saving.amount`` across all of the member's savings.

    The cache figure the ledger should reconcile to.
    """
    from services.models import Saving

    total = Saving.objects.filter(
        membership=membership,
    ).aggregate(total=Sum('amount'))['total'] or ZERO
    return total.quantize(MONEY_QUANTIZER, rounding=ROUND_HALF_UP)


def _get_reference_prefix(category):
    return CATEGORY_PREFIXES.get(category, 'LED')
