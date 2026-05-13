"""Bulk database operations for SACCO member import workflows."""

from decimal import Decimal
from uuid import uuid4

from django.db import transaction
from django.utils import timezone

from accounts.models import User
from ledger.models import LedgerEntry
from saccomembership.models import Membership
from services.models import Saving, SavingsType


class ImportAbortError(Exception):
    """Raised when DB failure rate exceeds the allowed threshold."""


class ImportParseError(Exception):
    """Raised when import payload structure cannot be processed."""


def import_members_to_sacco(valid_rows, sacco, imported_by):
    """
    Bulk import member records into a SACCO.

    Uses atomic transactions and rolls back the full import when database
    failures exceed five percent of input rows.
    """
    total_rows = len(valid_rows)
    success_count = 0
    fail_count = 0
    errors = []
    batch_size = 500

    if total_rows == 0:
        return {
            'success_count': 0,
            'fail_count': 0,
            'errors': [],
            'total_rows': 0,
        }

    with transaction.atomic():
        for batch_start in range(0, total_rows, batch_size):
            batch = valid_rows[batch_start:batch_start + batch_size]

            with transaction.atomic(savepoint=True):
                for batch_index, row in enumerate(batch, start=1):
                    row_number = batch_start + batch_index
                    try:
                        with transaction.atomic(savepoint=True):
                            _import_single_row(
                                row=row,
                                sacco=sacco,
                                imported_by=imported_by,
                            )
                        success_count += 1
                    except Exception as exc:  # pragma: no cover
                        fail_count += 1
                        errors.append(
                            {
                                'row_number': row_number,
                                'email': row.get('email'),
                                'error': str(exc),
                            },
                        )

                if (Decimal(fail_count) / Decimal(total_rows)) > Decimal('0.05'):
                    raise ImportAbortError('Too many failures — rolling back')

    return {
        'success_count': success_count,
        'fail_count': fail_count,
        'errors': errors,
        'total_rows': total_rows,
    }


def _import_single_row(row, sacco, imported_by):
    """Create or update User, Membership, and optional savings for one row."""
    user_defaults = {
        'first_name': row.get('first_name', ''),
        'last_name': row.get('last_name', ''),
        'phone_number': row.get('phone') or None,
    }
    user, created = User.objects.get_or_create(
        email=row['email'],
        defaults=user_defaults,
    )
    if created:
        user.set_unusable_password()
        user.save(update_fields=['password'])

    member_number = row.get('member_number') or None
    membership, _ = Membership.objects.update_or_create(
        user=user,
        sacco=sacco,
        defaults={
            'status': Membership.Status.APPROVED,
            'member_number': member_number,
            'approved_date': timezone.now(),
        },
    )

    savings_amount = row.get('savings_amount') or Decimal('0')
    if savings_amount > Decimal('0'):
        savings_type_name = (row.get('savings_type') or '').strip().upper()
        savings_type = None
        if savings_type_name:
            savings_type, _ = SavingsType.objects.get_or_create(
                sacco=sacco,
                name=savings_type_name,
                defaults={
                    'minimum_contribution': Decimal('0.00'),
                },
            )

        saving, _ = Saving.objects.get_or_create(
            membership=membership,
            savings_type=savings_type,
            defaults={
                'amount': Decimal('0.00'),
                'total_contributions': Decimal('0.00'),
                'status': Saving.Status.ACTIVE,
            },
        )
        saving.amount = (saving.amount or Decimal('0.00')) + savings_amount
        saving.total_contributions = (
            (saving.total_contributions or Decimal('0.00')) + savings_amount
        )
        saving.status = Saving.Status.ACTIVE
        saving.last_transaction_date = timezone.localdate()
        saving.save(
            update_fields=[
                'amount',
                'total_contributions',
                'status',
                'last_transaction_date',
                'updated_at',
            ],
        )

        LedgerEntry.objects.create(
            membership=membership,
            entry_type=LedgerEntry.EntryType.CREDIT,
            category=LedgerEntry.Category.SAVING_DEPOSIT,
            amount=savings_amount,
            reference=f'IMPORT-{uuid4().hex[:18].upper()}',
            description=(
                f'Bulk member import by {imported_by.email} '
                f'for {sacco.name}.'
            ),
            balance_after=saving.amount,
        )
