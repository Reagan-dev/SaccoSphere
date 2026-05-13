from datetime import timedelta
from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from ledger.engines.balance_calculator import get_balance_at_date
from ledger.models import LedgerEntry


ZERO = Decimal('0.00')


def build_statement(membership, from_date, to_date, requesting_user=None):
    """
    Build a member financial statement for a SACCO and date range.

    The returned dictionary is ready for API serialization and PDF generation.
    """
    opening_balance = get_balance_at_date(
        membership,
        from_date - timedelta(days=1),
    )
    entries = LedgerEntry.objects.filter(
        membership=membership,
        created_at__date__gte=from_date,
        created_at__date__lte=to_date,
    ).order_by('created_at').select_related('transaction')
    closing_balance = get_balance_at_date(membership, to_date)
    total_credits = entries.filter(
        entry_type=LedgerEntry.EntryType.CREDIT,
    ).aggregate(
        Sum('amount'),
    )['amount__sum'] or ZERO
    total_debits = entries.filter(
        entry_type=LedgerEntry.EntryType.DEBIT,
    ).aggregate(
        Sum('amount'),
    )['amount__sum'] or ZERO

    statement = {
        'member_name': membership.user.get_full_name(),
        'member_number': membership.member_number,
        'sacco_name': membership.sacco.name,
        'sacco_logo_url': _get_sacco_logo_url(membership),
        'from_date': from_date,
        'to_date': to_date,
        'generated_at': timezone.now(),
        'opening_balance': opening_balance,
        'closing_balance': closing_balance,
        'total_credits': total_credits,
        'total_debits': total_debits,
        'entries': [_serialize_entry(entry) for entry in entries],
        'currency': 'KES',
    }
    _record_statement_access(membership, requesting_user)
    return statement


def _serialize_entry(entry):
    return {
        'entry_type': entry.entry_type,
        'category': entry.category,
        'amount': entry.amount,
        'description': entry.description,
        'reference': entry.reference,
        'balance_after': entry.balance_after,
        'created_at': entry.created_at,
    }


def _get_sacco_logo_url(membership):
    logo = membership.sacco.logo
    if not logo:
        return None

    try:
        return logo.url
    except ValueError:
        return None


def _record_statement_access(membership, requesting_user=None):
    try:
        from saccomanagement import create_data_consent_log
    except ImportError:
        return

    create_data_consent_log(
        user=requesting_user or membership.user,
        data_type='MEMBER_STATEMENT',
        reason='Self-service download',
    )
