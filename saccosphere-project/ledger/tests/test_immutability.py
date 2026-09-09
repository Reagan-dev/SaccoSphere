"""Item 5: a posted LedgerEntry is append-only at the model layer."""

from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from accounts.models import Sacco, User
from ledger.models import LedgerEntry
from ledger.utils import create_ledger_entry
from saccomembership.models import Membership


class LedgerEntryImmutabilityTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email='ledger-immutable@example.com',
            phone_number='254700006001',
            password='StrongPass1',
        )
        self.sacco = Sacco.objects.create(
            name='Ledger SACCO',
            registration_number='LEDG-001',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
        )
        self.membership = Membership.objects.create(
            user=self.user,
            sacco=self.sacco,
            status=Membership.Status.APPROVED,
            member_number='LEDG-M-001',
            approved_date=timezone.now(),
        )
        self.entry = create_ledger_entry(
            membership=self.membership,
            entry_type=LedgerEntry.EntryType.CREDIT,
            category=LedgerEntry.Category.SAVING_DEPOSIT,
            amount=Decimal('1000.00'),
            description='Initial deposit',
        )

    def test_posted_entry_cannot_be_saved_again(self):
        self.entry.amount = Decimal('9999.00')
        with self.assertRaises(PermissionError):
            self.entry.save()

        self.entry.refresh_from_db()
        self.assertEqual(self.entry.amount, Decimal('1000.00'))

    def test_posted_entry_cannot_be_deleted(self):
        with self.assertRaises(PermissionError):
            self.entry.delete()

        self.assertTrue(
            LedgerEntry.objects.filter(pk=self.entry.pk).exists()
        )

    def test_correction_is_a_new_offsetting_entry(self):
        correction = create_ledger_entry(
            membership=self.membership,
            entry_type=LedgerEntry.EntryType.DEBIT,
            category=LedgerEntry.Category.ADJUSTMENT,
            amount=Decimal('1000.00'),
            description=f'Reverses {self.entry.reference}',
        )

        self.assertNotEqual(correction.pk, self.entry.pk)
        self.assertEqual(correction.balance_after, Decimal('0.00'))
        self.assertEqual(LedgerEntry.objects.count(), 2)

    def test_fresh_unsaved_entry_still_saves(self):
        # The guard must key on "already persisted", not "any save".
        entry = LedgerEntry(
            membership=self.membership,
            entry_type=LedgerEntry.EntryType.CREDIT,
            category=LedgerEntry.Category.SAVING_DEPOSIT,
            amount=Decimal('5.00'),
            reference='LEDG-FRESH-1',
            description='fresh',
            balance_after=Decimal('1005.00'),
        )
        entry.save()
        self.assertIsNotNone(entry.pk)
