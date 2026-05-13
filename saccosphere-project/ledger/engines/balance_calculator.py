from datetime import datetime
from decimal import Decimal
from uuid import uuid4

from django.db.models import Sum

from ledger.models import LedgerEntry


ZERO = Decimal('0.00')


def get_running_balance(membership, as_of_date=None):
    """
    Calculate a member's ledger balance up to a given date.

    LedgerEntry is the source of truth for this calculation, not Saving.amount.
    """
    queryset = LedgerEntry.objects.filter(membership=membership)
    if as_of_date:
        queryset = queryset.filter(created_at__date__lte=as_of_date)

    credits = queryset.filter(
        entry_type=LedgerEntry.EntryType.CREDIT,
    ).aggregate(
        Sum('amount'),
    )['amount__sum'] or ZERO
    debits = queryset.filter(
        entry_type=LedgerEntry.EntryType.DEBIT,
    ).aggregate(
        Sum('amount'),
    )['amount__sum'] or ZERO

    return credits - debits


def get_balance_at_date(membership, date):
    """Return a member's ledger balance at a specific date."""
    return get_running_balance(membership, as_of_date=date)


def generate_reference(prefix):
    """Generate a unique human-readable ledger reference."""
    timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
    suffix = uuid4().hex[:6].upper()
    return f'{prefix}-{timestamp}-{suffix}'
