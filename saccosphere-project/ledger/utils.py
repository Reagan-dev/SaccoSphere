from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction as db_transaction

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
}


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

    This is the only supported way to create ledger entries. The membership's
    existing ledger rows are locked while the new running balance is computed
    and written, so concurrent writes for the same membership are serialized.
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


def _get_reference_prefix(category):
    return CATEGORY_PREFIXES.get(category, 'LED')
