"""Defense-in-depth around LedgerEntry.amount staying positive.

create_ledger_entry() rejects a non-positive amount in Python, and the
ledger_entry_amount_positive DB CheckConstraint is the backstop for any
path that bypasses that - a direct .create() call, or a bulk .update()
that skips the append-only save() guard entirely.
"""

from decimal import Decimal

from django.db import IntegrityError, transaction
from django.test import TestCase

from accounts.models import Sacco, User
from ledger.models import LedgerEntry
from ledger.utils import create_ledger_entry
from saccomembership.models import Membership


class LedgerEntryAmountConstraintTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email='ledger-constraints@example.com',
            phone_number='254700009001',
            password='StrongPass1',
        )
        self.sacco = Sacco.objects.create(
            name='Constraints SACCO',
            registration_number='CONST-1',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
        )
        self.membership = Membership.objects.create(
            user=self.user,
            sacco=self.sacco,
            status=Membership.Status.APPROVED,
            member_number='CONST-M-001',
        )

    def test_create_ledger_entry_rejects_non_positive_amount(self):
        for bad in (Decimal('0.00'), Decimal('-1.00')):
            with self.assertRaises(ValueError):
                create_ledger_entry(
                    membership=self.membership,
                    entry_type=LedgerEntry.EntryType.CREDIT,
                    category=LedgerEntry.Category.SAVING_DEPOSIT,
                    amount=bad,
                    description='bad',
                    reference=f'BAD-CLE-{bad}',
                )

    def test_db_constraint_blocks_a_bulk_update_bypass(self):
        """The append-only save() guard only covers .save() calls.

        QuerySet.update() skips model methods entirely, so it is the one
        way existing code could still write a non-positive amount. The
        DB constraint must catch what Python-level guards cannot.
        """
        entry = create_ledger_entry(
            membership=self.membership,
            entry_type=LedgerEntry.EntryType.CREDIT,
            category=LedgerEntry.Category.SAVING_DEPOSIT,
            amount=Decimal('100.00'),
            description='initial',
            reference='CONST-OK-1',
        )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                LedgerEntry.objects.filter(pk=entry.pk).update(amount=0)
