import logging

from .engines.balance_calculator import (
    generate_reference,
    get_running_balance,
)
from .models import LedgerEntry


logger = logging.getLogger('saccosphere.ledger')


CATEGORY_PREFIXES = {
    LedgerEntry.Category.SAVING_DEPOSIT: 'SAV',
    LedgerEntry.Category.SAVING_WITHDRAWAL: 'SAV',
    LedgerEntry.Category.LOAN_DISBURSEMENT: 'LOAN',
    LedgerEntry.Category.LOAN_REPAYMENT: 'REP',
    LedgerEntry.Category.FEE: 'FEE',
    LedgerEntry.Category.PENALTY: 'FEE',
    LedgerEntry.Category.DIVIDEND: 'SAV',
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

    This is the only supported way to create ledger entries. It never raises to
    calling code; failures are logged and None is returned.
    """
    try:
        if reference is None:
            reference = generate_reference(_get_reference_prefix(category))

        balance_before = get_running_balance(membership)
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
    except Exception:
        logger.exception(
            'Failed to create ledger entry for membership_id=%s.',
            getattr(membership, 'id', None),
        )
        return None


def _get_reference_prefix(category):
    return CATEGORY_PREFIXES.get(category, 'LED')
