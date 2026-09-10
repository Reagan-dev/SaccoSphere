"""DividendPayout is a two-state machine: PENDING -> PAID.

``CREDITED`` was removed (migration 0018) - it was never set and never
meant anything distinct from PAID (a dividend is reinvested straight into
the member's savings ledger, so "credited" and "paid" are one event).
"""

from datetime import date
from decimal import Decimal

from django.test import TestCase

from accounts.models import Sacco, User
from ledger.models import LedgerEntry
from saccomembership.models import Membership
from services.engines.dividend_calculator import (
    calculate_dividends_for_declaration,
)
from services.engines.dividend_disbursement import (
    disburse_dividends_for_declaration,
)
from services.models import (
    DividendDeclaration,
    DividendPayout,
    Saving,
    SavingsType,
)


class DividendPayoutStateMachineTests(TestCase):
    def setUp(self):
        self.sacco = Sacco.objects.create(
            name='Payout FSM SACCO',
            registration_number='PFSM-1',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
        )
        self.savings_type = SavingsType.objects.create(
            sacco=self.sacco,
            name=SavingsType.Name.BOSA,
            minimum_contribution=Decimal('100.00'),
        )
        self._member_seq = 0

    def _saving(self, amount='0.00', dividend_eligible=True):
        self._member_seq += 1
        user = User.objects.create_user(
            email=f'pfsm-m{self._member_seq}@example.com',
            password='StrongPass1',
        )
        membership = Membership.objects.create(
            user=user,
            sacco=self.sacco,
            status=Membership.Status.APPROVED,
            member_number=f'PFSM-M{self._member_seq:03d}',
        )
        return Saving.objects.create(
            membership=membership,
            savings_type=self.savings_type,
            amount=Decimal(amount),
            status=Saving.Status.ACTIVE,
            dividend_eligible=dividend_eligible,
        )

    def _declaration(self, status, financial_year='2025/2026'):
        return DividendDeclaration.objects.create(
            sacco=self.sacco,
            savings_type=self.savings_type,
            financial_year=financial_year,
            declared_rate=Decimal('10.00'),
            period_start=date(2025, 1, 1),
            period_end=date(2025, 12, 31),
            status=status,
        )

    def _payout(self, declaration, saving, amount, status=None):
        return DividendPayout.objects.create(
            declaration=declaration,
            membership=saving.membership,
            saving=saving,
            average_balance=Decimal('10000.00'),
            dividend_amount=Decimal(amount),
            status=status or DividendPayout.Status.PENDING,
        )

    # --- the documented state model ---------------------------------

    def test_status_enum_is_exactly_pending_and_paid(self):
        self.assertEqual(
            set(DividendPayout.Status.values), {'PENDING', 'PAID'},
        )
        self.assertFalse(hasattr(DividendPayout.Status, 'CREDITED'))
        self.assertEqual(
            DividendPayout._meta.get_field('status').default, 'PENDING',
        )

    # --- PENDING is where a payout starts --------------------------

    def test_calculator_creates_payouts_in_pending(self):
        self._saving(amount='5000.00')
        declaration = self._declaration(DividendDeclaration.Status.DRAFT)

        calculate_dividends_for_declaration(declaration)

        statuses = set(
            declaration.payouts.values_list('status', flat=True)
        )
        self.assertEqual(statuses, {DividendPayout.Status.PENDING})

    # --- PENDING -> PAID via disburse ------------------------------

    def test_disburse_moves_pending_to_paid_and_posts_the_ledger(self):
        saving_a = self._saving()
        saving_b = self._saving()
        declaration = self._declaration(DividendDeclaration.Status.APPROVED)
        self._payout(declaration, saving_a, '1000.00')
        self._payout(declaration, saving_b, '1500.00')

        result = disburse_dividends_for_declaration(declaration)

        self.assertEqual(result['paid_count'], 2)
        self.assertEqual(
            set(declaration.payouts.values_list('status', flat=True)),
            {DividendPayout.Status.PAID},
        )
        declaration.refresh_from_db()
        self.assertEqual(
            declaration.status, DividendDeclaration.Status.DISBURSED,
        )
        saving_a.refresh_from_db()
        saving_b.refresh_from_db()
        self.assertEqual(saving_a.amount, Decimal('1000.00'))
        self.assertEqual(saving_b.amount, Decimal('1500.00'))
        self.assertEqual(
            LedgerEntry.objects.filter(
                category=LedgerEntry.Category.DIVIDEND_PAYOUT,
            ).count(),
            2,
        )

    def test_zero_amount_payout_goes_straight_to_paid_no_ledger(self):
        saving = self._saving()
        declaration = self._declaration(DividendDeclaration.Status.APPROVED)
        self._payout(declaration, saving, '0.00')

        result = disburse_dividends_for_declaration(declaration)

        self.assertEqual(result['paid_count'], 1)
        self.assertEqual(
            declaration.payouts.get().status, DividendPayout.Status.PAID,
        )
        self.assertFalse(
            LedgerEntry.objects.filter(
                membership=saving.membership,
                category=LedgerEntry.Category.DIVIDEND_PAYOUT,
            ).exists()
        )

    # --- PAID is terminal: no re-processing -----------------------

    def test_disburse_leaves_already_paid_payouts_untouched(self):
        pending_saving = self._saving()
        paid_saving = self._saving()
        declaration = self._declaration(DividendDeclaration.Status.APPROVED)
        self._payout(declaration, pending_saving, '1000.00')
        paid_payout = self._payout(
            declaration, paid_saving, '2000.00',
            status=DividendPayout.Status.PAID,
        )

        result = disburse_dividends_for_declaration(declaration)

        # Only the PENDING one was moved / credited.
        self.assertEqual(result['paid_count'], 1)
        paid_payout.refresh_from_db()
        self.assertEqual(paid_payout.status, DividendPayout.Status.PAID)
        paid_saving.refresh_from_db()
        self.assertEqual(paid_saving.amount, Decimal('0.00'))
        self.assertEqual(
            LedgerEntry.objects.filter(
                category=LedgerEntry.Category.DIVIDEND_PAYOUT,
            ).count(),
            1,
        )

    def test_a_stray_credited_row_is_never_reprocessed_by_disburse(self):
        # Only a manual pre-0018 admin edit could produce this; disburse
        # filters status=PENDING, so it would be stuck forever - which is
        # exactly why migration 0018 re-labels it to PAID.
        saving = self._saving()
        declaration = self._declaration(DividendDeclaration.Status.APPROVED)
        payout = self._payout(declaration, saving, '1000.00')
        DividendPayout.objects.filter(pk=payout.pk).update(status='CREDITED')

        result = disburse_dividends_for_declaration(declaration)

        self.assertEqual(result['paid_count'], 0)
        payout.refresh_from_db()
        self.assertEqual(payout.status, 'CREDITED')
        saving.refresh_from_db()
        self.assertEqual(saving.amount, Decimal('0.00'))
        self.assertFalse(LedgerEntry.objects.exists())
