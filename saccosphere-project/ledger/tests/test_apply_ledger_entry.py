"""apply_ledger_entry is the single funnel for Saving.amount changes.

Every deposit / withdrawal / dividend-credit test asserts
``saving.amount == <the ledger's savings balance>`` afterwards.
"""

from decimal import Decimal

from django.test import TestCase

from accounts.models import Sacco, User
from ledger.models import LedgerEntry
from ledger.utils import (
    apply_ledger_entry,
    expected_savings_balance,
    savings_ledger_balance,
)
from saccomembership.models import Membership
from services.models import Saving, SavingsType


class ApplyLedgerEntryTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email='apply-ledger@example.com',
            phone_number='254700007001',
            password='StrongPass1',
        )
        self.sacco = Sacco.objects.create(
            name='Apply Ledger SACCO',
            registration_number='APLDG-1',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
        )
        self.membership = Membership.objects.create(
            user=self.user,
            sacco=self.sacco,
            status=Membership.Status.APPROVED,
            member_number='APLDG-M-001',
        )
        self.savings_type = SavingsType.objects.create(
            sacco=self.sacco,
            name=SavingsType.Name.BOSA,
            minimum_contribution=Decimal('100.00'),
        )
        self.saving = Saving.objects.create(
            membership=self.membership,
            savings_type=self.savings_type,
            amount=Decimal('0.00'),
            status=Saving.Status.ACTIVE,
        )

    def _reload(self):
        self.saving.refresh_from_db()
        return self.saving

    def test_rejects_non_positive_amount(self):
        for bad in (Decimal('0.00'), Decimal('-1.00')):
            with self.assertRaises(ValueError):
                apply_ledger_entry(
                    saving=self.saving,
                    amount=bad,
                    entry_type=LedgerEntry.EntryType.CREDIT,
                    category=LedgerEntry.Category.SAVING_DEPOSIT,
                    description='bad',
                    reference=f'BAD-{bad}',
                )

    def test_deposit_credit_moves_amount_and_matches_ledger(self):
        entry = apply_ledger_entry(
            saving=self.saving,
            amount=Decimal('5000.00'),
            entry_type=LedgerEntry.EntryType.CREDIT,
            category=LedgerEntry.Category.SAVING_DEPOSIT,
            description='deposit',
            reference='DEP-1',
            contribution_delta=Decimal('5000.00'),
        )
        self.assertEqual(entry.entry_type, LedgerEntry.EntryType.CREDIT)
        # instance mutated in place
        self.assertEqual(self.saving.amount, Decimal('5000.00'))
        db = self._reload()
        self.assertEqual(db.amount, Decimal('5000.00'))
        self.assertEqual(db.total_contributions, Decimal('5000.00'))
        self.assertEqual(
            db.amount, savings_ledger_balance(self.membership),
        )

    def test_withdrawal_debit_reduces_amount_and_matches_ledger(self):
        apply_ledger_entry(
            saving=self.saving, amount=Decimal('8000.00'),
            entry_type=LedgerEntry.EntryType.CREDIT,
            category=LedgerEntry.Category.SAVING_DEPOSIT,
            description='deposit', reference='DEP-2',
            contribution_delta=Decimal('8000.00'),
        )
        apply_ledger_entry(
            saving=self.saving, amount=Decimal('3000.00'),
            entry_type=LedgerEntry.EntryType.DEBIT,
            category=LedgerEntry.Category.SAVING_WITHDRAWAL,
            description='withdrawal', reference='WD-2',
            withdrawal_delta=Decimal('3000.00'),
        )
        db = self._reload()
        self.assertEqual(db.amount, Decimal('5000.00'))
        self.assertEqual(db.total_contributions, Decimal('8000.00'))
        self.assertEqual(db.total_withdrawals, Decimal('3000.00'))
        self.assertEqual(
            db.amount, savings_ledger_balance(self.membership),
        )

    def test_withdrawal_reversal_credit_nets_to_zero(self):
        apply_ledger_entry(
            saving=self.saving, amount=Decimal('2000.00'),
            entry_type=LedgerEntry.EntryType.DEBIT,
            category=LedgerEntry.Category.SAVING_WITHDRAWAL,
            description='withdrawal', reference='WD-3',
            withdrawal_delta=Decimal('2000.00'),
        )
        apply_ledger_entry(
            saving=self.saving, amount=Decimal('2000.00'),
            entry_type=LedgerEntry.EntryType.CREDIT,
            category=LedgerEntry.Category.SAVING_WITHDRAWAL,
            description='reversed', reference='WD-3-REV',
            withdrawal_delta=Decimal('-2000.00'),
        )
        db = self._reload()
        self.assertEqual(db.amount, Decimal('0.00'))
        self.assertEqual(db.total_withdrawals, Decimal('0.00'))
        self.assertEqual(
            db.amount, savings_ledger_balance(self.membership),
        )

    def test_dividend_credit_matches_ledger(self):
        apply_ledger_entry(
            saving=self.saving, amount=Decimal('10000.00'),
            entry_type=LedgerEntry.EntryType.CREDIT,
            category=LedgerEntry.Category.SAVING_DEPOSIT,
            description='deposit', reference='DEP-4',
            contribution_delta=Decimal('10000.00'),
        )
        apply_ledger_entry(
            saving=self.saving, amount=Decimal('1234.56'),
            entry_type=LedgerEntry.EntryType.CREDIT,
            category=LedgerEntry.Category.DIVIDEND_PAYOUT,
            description='dividend 2025/2026', reference='DIV-4',
        )
        db = self._reload()
        self.assertEqual(db.amount, Decimal('11234.56'))
        self.assertEqual(
            db.amount, savings_ledger_balance(self.membership),
        )
        self.assertEqual(
            expected_savings_balance(self.membership), db.amount,
        )

    def test_loan_ledger_rows_do_not_count_toward_savings_balance(self):
        apply_ledger_entry(
            saving=self.saving, amount=Decimal('4000.00'),
            entry_type=LedgerEntry.EntryType.CREDIT,
            category=LedgerEntry.Category.SAVING_DEPOSIT,
            description='deposit', reference='DEP-5',
            contribution_delta=Decimal('4000.00'),
        )
        # A loan disbursement debit on the same membership must not
        # perturb the savings figure.
        from ledger.utils import create_ledger_entry
        create_ledger_entry(
            membership=self.membership,
            entry_type=LedgerEntry.EntryType.DEBIT,
            category=LedgerEntry.Category.LOAN_DISBURSEMENT,
            amount=Decimal('50000.00'),
            description='loan out',
            reference='LOAN-5',
        )
        self.assertEqual(
            savings_ledger_balance(self.membership), Decimal('4000.00'),
        )
        self.assertEqual(
            self._reload().amount, Decimal('4000.00'),
        )
