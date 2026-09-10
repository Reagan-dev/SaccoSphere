"""Backfill + daily reconciliation for the savings-ledger single source.

- backfill_savings_opening_balances plants the missing OPENING_BALANCE
  entry so a phantom opening balance reconciles;
- reconcile_savings_ledger flags-and-alerts a fresh drift, and never
  silently corrects it.
"""

from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.db.models import F
from django.test import TestCase

from accounts.models import Sacco, User
from ledger.models import LedgerEntry
from ledger.utils import apply_ledger_entry, savings_ledger_balance
from saccomanagement.models import ComplianceFlag, SystemAuditLog
from saccomembership.models import Membership
from services.models import Saving, SavingsType
from services.tasks import _run_savings_ledger_reconciliation


def _make_member(sacco, email, member_number, amount):
    user = User.objects.create_user(
        email=email, phone_number=None, password='StrongPass1',
    )
    membership = Membership.objects.create(
        user=user, sacco=sacco, status=Membership.Status.APPROVED,
        member_number=member_number,
    )
    stype = SavingsType.objects.create(
        sacco=sacco, name=SavingsType.Name.BOSA,
        minimum_contribution=Decimal('100.00'),
    )
    saving = Saving.objects.create(
        membership=membership, savings_type=stype,
        amount=Decimal(amount), total_contributions=Decimal(amount),
        status=Saving.Status.ACTIVE,
    )
    return membership, saving


class BackfillOpeningBalancesTest(TestCase):
    def setUp(self):
        self.sacco = Sacco.objects.create(
            name='Backfill SACCO', registration_number='BKFL-1',
            sector=Sacco.Sector.FINANCE, county='Nairobi',
        )
        # Phantom opening balance: Saving.amount set, zero ledger rows.
        self.membership, self.saving = _make_member(
            self.sacco, 'bkfl@example.com', 'BKFL-M-001', '11000.00',
        )

    def test_backfill_makes_the_ledger_match(self):
        self.assertEqual(
            savings_ledger_balance(self.membership), Decimal('0.00'),
        )

        out = StringIO()
        call_command(
            'backfill_savings_opening_balances',
            '--sacco', str(self.sacco.id), stdout=out,
        )

        opening = LedgerEntry.objects.get(
            membership=self.membership,
            category=LedgerEntry.Category.OPENING_BALANCE,
        )
        self.assertEqual(opening.entry_type, LedgerEntry.EntryType.CREDIT)
        self.assertEqual(opening.amount, Decimal('11000.00'))
        self.assertEqual(opening.reference, f'OPENING-{self.membership.id}')
        self.assertEqual(
            savings_ledger_balance(self.membership), Decimal('11000.00'),
        )
        # Saving.amount was never touched.
        self.saving.refresh_from_db()
        self.assertEqual(self.saving.amount, Decimal('11000.00'))

    def test_backfill_is_resumable_and_dry_run_writes_nothing(self):
        call_command(
            'backfill_savings_opening_balances', '--sacco',
            str(self.sacco.id), '--dry-run', stdout=StringIO(),
        )
        self.assertFalse(
            LedgerEntry.objects.filter(
                category=LedgerEntry.Category.OPENING_BALANCE,
            ).exists()
        )

        call_command(
            'backfill_savings_opening_balances', '--sacco',
            str(self.sacco.id), stdout=StringIO(),
        )
        call_command(
            'backfill_savings_opening_balances', '--sacco',
            str(self.sacco.id), stdout=StringIO(),
        )
        self.assertEqual(
            LedgerEntry.objects.filter(
                category=LedgerEntry.Category.OPENING_BALANCE,
            ).count(),
            1,
        )


class ReconcileSavingsLedgerTest(TestCase):
    def setUp(self):
        self.sacco = Sacco.objects.create(
            name='Recon SACCO A', registration_number='RCON-A',
            sector=Sacco.Sector.FINANCE, county='Nairobi',
        )
        self.other_sacco = Sacco.objects.create(
            name='Recon SACCO B', registration_number='RCON-B',
            sector=Sacco.Sector.FINANCE, county='Kiambu',
        )
        self.membership, self.saving = _make_member(
            self.sacco, 'rcon-a@example.com', 'RCON-A-M1', '0.00',
        )
        # A clean, fully-ledgered deposit for member A.
        apply_ledger_entry(
            saving=self.saving, amount=Decimal('6000.00'),
            entry_type=LedgerEntry.EntryType.CREDIT,
            category=LedgerEntry.Category.SAVING_DEPOSIT,
            description='deposit', reference='RCON-DEP-1',
            contribution_delta=Decimal('6000.00'),
        )
        self.other_membership, self.other_saving = _make_member(
            self.other_sacco, 'rcon-b@example.com', 'RCON-B-M1', '0.00',
        )
        apply_ledger_entry(
            saving=self.other_saving, amount=Decimal('9000.00'),
            entry_type=LedgerEntry.EntryType.CREDIT,
            category=LedgerEntry.Category.SAVING_DEPOSIT,
            description='deposit', reference='RCON-DEP-2',
            contribution_delta=Decimal('9000.00'),
        )

    def test_clean_book_raises_no_flag(self):
        result = _run_savings_ledger_reconciliation()
        self.assertEqual(result['memberships_mismatched'], 0)
        self.assertEqual(result['saccos_flagged'], 0)
        self.assertFalse(
            ComplianceFlag.objects.filter(
                flag_type=ComplianceFlag.FlagType.DATA_DISCREPANCY,
            ).exists()
        )

    def test_manual_mismatch_is_detected_and_alerted_not_fixed(self):
        # Bypass apply_ledger_entry entirely - the drift this control
        # exists to catch.
        Saving.objects.filter(pk=self.saving.pk).update(
            amount=F('amount') + Decimal('500.00'),
        )

        result = _run_savings_ledger_reconciliation()

        self.assertEqual(result['memberships_mismatched'], 1)
        self.assertEqual(result['saccos_flagged'], 1)

        flag = ComplianceFlag.objects.get(
            sacco=self.sacco,
            flag_type=ComplianceFlag.FlagType.DATA_DISCREPANCY,
        )
        self.assertEqual(flag.severity, ComplianceFlag.Severity.HIGH)
        self.assertEqual(flag.metadata['mismatched_memberships'], 1)
        self.assertEqual(flag.metadata['total_drift'], '500.00')

        self.assertTrue(
            SystemAuditLog.objects.filter(
                action='SAVINGS_LEDGER_MISMATCH',
                resource_type='Sacco',
                resource_id=str(self.sacco.id),
            ).exists()
        )

        # The other tenant is untouched.
        self.assertFalse(
            ComplianceFlag.objects.filter(sacco=self.other_sacco).exists()
        )

        # NOT auto-corrected.
        self.saving.refresh_from_db()
        self.assertEqual(self.saving.amount, Decimal('6500.00'))
        self.assertEqual(
            savings_ledger_balance(self.membership), Decimal('6000.00'),
        )
