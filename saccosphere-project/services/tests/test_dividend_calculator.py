"""Tests for dividend calculation engine and views."""

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, connection, transaction
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import Sacco, SaccoSettings, User
from ledger.models import LedgerEntry
from ledger.utils import apply_ledger_entry, create_ledger_entry
from saccomanagement.models import Role, SystemAuditLog
from saccomembership.models import Membership
from services.engines.dividend_calculator import (
    calculate_average_balance,
    calculate_dividends_for_declaration,
)
from payments.models import MpesaTransaction, PaymentProvider, Transaction
from services.models import (
    DividendDeclaration,
    DividendPayout,
    Saving,
    SavingsType,
)


class DividendCalculatorTests(TestCase):
    """Test dividend calculation logic."""

    def setUp(self):
        self.sacco = Sacco.objects.create(
            name='Dividend Test SACCO',
            registration_number='DIV001',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
        )
        self.savings_type = SavingsType.objects.create(
            sacco=self.sacco,
            name=SavingsType.Name.BOSA,
            minimum_contribution=Decimal('500.00'),
        )
        self.user = User.objects.create_user(
            email='member@example.com',
            password='secret',
            phone_number='254712345678',
        )
        self.membership = Membership.objects.create(
            user=self.user,
            sacco=self.sacco,
            status=Membership.Status.APPROVED,
            member_number='DIV-M001',
        )
        self.saving = Saving.objects.create(
            membership=self.membership,
            savings_type=self.savings_type,
            amount=Decimal('10000.00'),
            status=Saving.Status.ACTIVE,
            dividend_eligible=True,
        )

    def test_average_balance_calculates_month_end_averages(self):
        period_start = date(2025, 1, 1)
        period_end = date(2025, 12, 31)

        average = calculate_average_balance(
            self.saving,
            period_start,
            period_end,
        )

        self.assertIsInstance(average, Decimal)
        self.assertEqual(
            average.as_tuple().exponent,
            -2,
            'Average should be rounded to 2 decimal places',
        )
        # self.saving carries a 10 000.00 seeded opening balance and has
        # no ledger / M-Pesa history at all: every month-end samples
        # 10 000.00, so the average is exactly that. The old
        # M-Pesa-only reconstruction returned ~0.00 here - the reported
        # bug.
        self.assertEqual(average, Decimal('10000.00'))

    def test_average_balance_uses_specific_saving_history(self):
        """Reconstruction unwinds only THIS account's later movements."""
        other_savings_type = SavingsType.objects.create(
            sacco=self.sacco,
            name=SavingsType.Name.FOSA,
            minimum_contribution=Decimal('500.00'),
        )
        other_saving = Saving.objects.create(
            membership=self.membership,
            savings_type=other_savings_type,
            amount=Decimal('900.00'),
            status=Saving.Status.ACTIVE,
            dividend_eligible=True,
        )
        provider = PaymentProvider.objects.create(
            name='M-Pesa',
            provider_type=PaymentProvider.ProviderType.MPESA,
            is_active=True,
        )
        # Later (June) M-Pesa deposits on BOTH accounts.
        self._create_saving_ledger_entry(
            provider,
            self.saving,
            Decimal('2000.00'),
            'DIV-SPECIFIC-001',
            when=date(2025, 6, 15),
        )
        self._create_saving_ledger_entry(
            provider,
            other_saving,
            Decimal('4000.00'),
            'DIV-SPECIFIC-002',
            when=date(2025, 6, 15),
        )

        # As of end-January: only self.saving's own 2 000 June deposit
        # is unwound (10 000 - 2 000); other_saving's 4 000 is ignored.
        average = calculate_average_balance(
            self.saving,
            date(2025, 1, 1),
            date(2025, 1, 31),
        )

        self.assertEqual(average, Decimal('8000.00'))

    def _create_saving_ledger_entry(
        self,
        provider,
        saving,
        amount,
        reference,
        when=date(2025, 1, 15),
    ):
        transaction = Transaction.objects.create(
            provider=provider,
            user=self.user,
            reference=f'{reference}-TXN',
            transaction_type=Transaction.TransactionType.DEPOSIT,
            amount=amount,
            status=Transaction.Status.COMPLETED,
            description='Dividend balance history test',
        )
        MpesaTransaction.objects.create(
            transaction=transaction,
            phone_number='254712345678',
            checkout_request_id=f'{reference}-CHECKOUT',
            related_saving=saving,
        )
        entry = create_ledger_entry(
            membership=self.membership,
            entry_type=LedgerEntry.EntryType.CREDIT,
            category=LedgerEntry.Category.SAVING_DEPOSIT,
            amount=amount,
            description='Dividend balance history test',
            reference=reference,
            transaction=transaction,
        )
        created_at = timezone.make_aware(
            datetime.combine(when, time(hour=12)),
        )
        LedgerEntry.objects.filter(id=entry.id).update(created_at=created_at)
        return entry

    def test_average_balance_includes_prior_dividend_and_adjustment(self):
        """Non-M-Pesa movements are part of the reconstructed balance."""
        # Phantom opening balance (10 000.00) + a real prior-year
        # dividend credit + an admin adjustment, the last two written
        # through apply_ledger_entry so Saving.amount tracks them.
        prior_declaration = DividendDeclaration.objects.create(
            sacco=self.sacco,
            savings_type=self.savings_type,
            financial_year='2024/2025',
            declared_rate=Decimal('8.00'),
            period_start=date(2024, 1, 1),
            period_end=date(2024, 12, 31),
            status=DividendDeclaration.Status.DISBURSED,
        )
        prior_payout = DividendPayout.objects.create(
            declaration=prior_declaration,
            membership=self.membership,
            saving=self.saving,
            average_balance=Decimal('6250.00'),
            dividend_amount=Decimal('500.00'),
            status=DividendPayout.Status.PAID,
        )
        dividend_entry = apply_ledger_entry(
            saving=self.saving,
            amount=Decimal('500.00'),
            entry_type=LedgerEntry.EntryType.CREDIT,
            category=LedgerEntry.Category.DIVIDEND_PAYOUT,
            description='prior year dividend',
            reference=f'DIV-{prior_declaration.id}-{prior_payout.id}',
        )
        adjustment_entry = apply_ledger_entry(
            saving=self.saving,
            amount=Decimal('300.00'),
            entry_type=LedgerEntry.EntryType.CREDIT,
            category=LedgerEntry.Category.ADJUSTMENT,
            description='admin correction',
            reference='ADJ-HIST-1',
        )
        for entry in (dividend_entry, adjustment_entry):
            LedgerEntry.objects.filter(id=entry.id).update(
                created_at=timezone.make_aware(
                    datetime.combine(date(2025, 3, 10), time(hour=12)),
                ),
            )
        self.saving.refresh_from_db()
        self.assertEqual(self.saving.amount, Decimal('10800.00'))

        # A month-end AFTER both movements: nothing to unwind, so both
        # the dividend and the adjustment are reflected.
        after = calculate_average_balance(
            self.saving, date(2025, 6, 1), date(2025, 6, 30),
        )
        self.assertEqual(after, Decimal('10800.00'))

        # A month-end BEFORE both: the dividend credit is attributable
        # and unwound; the membership-level ADJUSTMENT has no
        # per-account link and stays in the baseline (documented
        # limitation - exact fix is a per-entry saving FK).
        before = calculate_average_balance(
            self.saving, date(2025, 2, 1), date(2025, 2, 28),
        )
        self.assertEqual(before, Decimal('10300.00'))

    def test_negative_reconstructed_balance_clamps_dividend_to_zero(self):
        provider = PaymentProvider.objects.create(
            name='M-Pesa',
            provider_type=PaymentProvider.ProviderType.MPESA,
            is_active=True,
        )
        # A large deposit dated AFTER the one-month period: every
        # month-end in the period unwinds it, driving the reconstructed
        # balance well negative (10 000 - 50 000).
        self._create_saving_ledger_entry(
            provider,
            self.saving,
            Decimal('50000.00'),
            'NEG-RECON-1',
            when=date(2025, 2, 15),
        )
        declaration = DividendDeclaration.objects.create(
            sacco=self.sacco,
            savings_type=self.savings_type,
            financial_year='2025/2026',
            declared_rate=Decimal('10.00'),
            period_start=date(2025, 1, 1),
            period_end=date(2025, 1, 31),
            status=DividendDeclaration.Status.DRAFT,
        )

        result = calculate_dividends_for_declaration(declaration)

        payout = declaration.payouts.get(saving=self.saving)
        self.assertLess(payout.average_balance, Decimal('0.00'))
        self.assertEqual(payout.dividend_amount, Decimal('0.00'))
        self.assertEqual(result['payout_count'], 1)
        declaration.refresh_from_db()
        self.assertEqual(
            declaration.total_dividend_amount, Decimal('0.00'),
        )
        self.assertTrue(
            SystemAuditLog.objects.filter(
                action='DIVIDEND_NEGATIVE_CLAMPED',
                resource_type='Saving',
                resource_id=str(self.saving.id),
            ).exists()
        )

    def test_balance_query_ignores_other_sacco_ledger_rows(self):
        other_sacco = Sacco.objects.create(
            name='Other Dividend SACCO',
            registration_number='DIV-OTHER',
            sector=Sacco.Sector.FINANCE,
            county='Kiambu',
        )
        other_type = SavingsType.objects.create(
            sacco=other_sacco,
            name=SavingsType.Name.BOSA,
            minimum_contribution=Decimal('500.00'),
        )
        other_user = User.objects.create_user(
            email='other-div-member@example.com',
            password='secret',
            phone_number='254799999999',
        )
        other_membership = Membership.objects.create(
            user=other_user,
            sacco=other_sacco,
            status=Membership.Status.APPROVED,
            member_number='DIV-O-001',
        )
        # Large movement on the OTHER SACCO's ledger, dated after the
        # as-of date - must never be summed into self.saving's balance.
        entry = create_ledger_entry(
            membership=other_membership,
            entry_type=LedgerEntry.EntryType.CREDIT,
            category=LedgerEntry.Category.SAVING_DEPOSIT,
            amount=Decimal('999999.00'),
            description='other sacco deposit',
            reference='OTHER-SACCO-DIV-1',
        )
        LedgerEntry.objects.filter(id=entry.id).update(
            created_at=timezone.make_aware(
                datetime.combine(date(2025, 6, 1), time(hour=12)),
            ),
        )

        average = calculate_average_balance(
            self.saving, date(2025, 1, 1), date(2025, 1, 31),
        )

        self.assertEqual(average, Decimal('10000.00'))

    def test_dividend_calculation_is_idempotent(self):
        declaration = DividendDeclaration.objects.create(
            sacco=self.sacco,
            savings_type=self.savings_type,
            financial_year='2025/2026',
            declared_rate=Decimal('10.00'),
            period_start=date(2025, 1, 1),
            period_end=date(2025, 12, 31),
            status=DividendDeclaration.Status.DRAFT,
        )
        
        # First calculation
        result1 = calculate_dividends_for_declaration(declaration)
        payout_count1 = declaration.payouts.count()
        
        # Second calculation (should delete and recreate)
        result2 = calculate_dividends_for_declaration(declaration)
        payout_count2 = declaration.payouts.count()
        
        self.assertEqual(payout_count1, payout_count2)
        self.assertEqual(result1['total_dividend_amount'], result2['total_dividend_amount'])

    def test_cannot_recalculate_approved_declaration(self):
        declaration = DividendDeclaration.objects.create(
            sacco=self.sacco,
            savings_type=self.savings_type,
            financial_year='2025/2026',
            declared_rate=Decimal('10.00'),
            period_start=date(2025, 1, 1),
            period_end=date(2025, 12, 31),
            status=DividendDeclaration.Status.APPROVED,
        )
        
        with self.assertRaises(ValueError) as context:
            calculate_dividends_for_declaration(declaration)
        
        self.assertIn('APPROVED', str(context.exception))

    def test_cannot_recalculate_disbursed_declaration(self):
        declaration = DividendDeclaration.objects.create(
            sacco=self.sacco,
            savings_type=self.savings_type,
            financial_year='2025/2026',
            declared_rate=Decimal('10.00'),
            period_start=date(2025, 1, 1),
            period_end=date(2025, 12, 31),
            status=DividendDeclaration.Status.DISBURSED,
        )
        
        with self.assertRaises(ValueError) as context:
            calculate_dividends_for_declaration(declaration)
        
        self.assertIn('DISBURSED', str(context.exception))

    def test_only_eligible_savings_receive_dividends(self):
        # Create ineligible saving
        ineligible_user = User.objects.create_user(
            email='ineligible@example.com',
            password='secret',
            phone_number='254712345679',
        )
        ineligible_membership = Membership.objects.create(
            user=ineligible_user,
            sacco=self.sacco,
            status=Membership.Status.APPROVED,
            member_number='DIV-M002',
        )
        ineligible_saving = Saving.objects.create(
            membership=ineligible_membership,
            savings_type=self.savings_type,
            amount=Decimal('5000.00'),
            status=Saving.Status.ACTIVE,
            dividend_eligible=False,  # Not eligible
        )
        
        declaration = DividendDeclaration.objects.create(
            sacco=self.sacco,
            savings_type=self.savings_type,
            financial_year='2025/2026',
            declared_rate=Decimal('10.00'),
            period_start=date(2025, 1, 1),
            period_end=date(2025, 12, 31),
            status=DividendDeclaration.Status.DRAFT,
        )
        
        result = calculate_dividends_for_declaration(declaration)
        
        # Only eligible saving should have payout
        self.assertEqual(result['payout_count'], 1)
        self.assertTrue(
            declaration.payouts.filter(saving=self.saving).exists()
        )
        self.assertFalse(
            declaration.payouts.filter(saving=ineligible_saving).exists()
        )


class DividendDeclarationAPITests(TestCase):
    """Test dividend declaration API endpoints."""

    def setUp(self):
        self.client = APIClient()
        self.sacco = Sacco.objects.create(
            name='Dividend API SACCO',
            registration_number='DIVAPI001',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
        )
        self.savings_type = SavingsType.objects.create(
            sacco=self.sacco,
            name=SavingsType.Name.BOSA,
            minimum_contribution=Decimal('500.00'),
        )
        self.admin = User.objects.create_user(
            email='dividend-admin@example.com',
            password='secret',
            phone_number='254712345670',
        )
        Role.objects.create(
            user=self.admin,
            sacco=self.sacco,
            name=Role.SACCO_ADMIN,
        )
        self.client.force_authenticate(user=self.admin)

    def test_create_dividend_declaration(self):
        response = self.client.post(
            '/api/v1/services/dividends/declarations/',
            {
                'savings_type': str(self.savings_type.id),
                'financial_year': '2025/2026',
                'declared_rate': '12.50',
                'period_start': '2025-01-01',
                'period_end': '2025-12-31',
            },
            format='json',
            HTTP_X_SACCO_ID=str(self.sacco.id),
        )
        
        if response.status_code != 201:
            print(f"Response status: {response.status_code}")
            print(f"Response data: {response.data if hasattr(response, 'data') else response.content}")
        
        self.assertEqual(response.status_code, 201)
        declaration = DividendDeclaration.objects.get(id=response.data['id'])
        self.assertEqual(declaration.status, DividendDeclaration.Status.DRAFT)
        self.assertEqual(declaration.declared_rate, Decimal('12.50'))

    def test_list_dividend_declarations(self):
        DividendDeclaration.objects.create(
            sacco=self.sacco,
            savings_type=self.savings_type,
            financial_year='2024/2025',
            declared_rate=Decimal('10.00'),
            period_start=date(2024, 1, 1),
            period_end=date(2024, 12, 31),
            status=DividendDeclaration.Status.DRAFT,
        )
        
        response = self.client.get(
            '/api/v1/services/dividends/declarations/',
            HTTP_X_SACCO_ID=str(self.sacco.id),
        )
        
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['data']), 1)

    def test_calculate_dividends_endpoint(self):
        declaration = DividendDeclaration.objects.create(
            sacco=self.sacco,
            savings_type=self.savings_type,
            financial_year='2025/2026',
            declared_rate=Decimal('10.00'),
            period_start=date(2025, 1, 1),
            period_end=date(2025, 12, 31),
            status=DividendDeclaration.Status.DRAFT,
        )
        
        response = self.client.post(
            f'/api/v1/services/dividends/declarations/{declaration.id}/calculate/',
            HTTP_X_SACCO_ID=str(self.sacco.id),
        )
        
        self.assertEqual(response.status_code, 200)
        declaration.refresh_from_db()
        self.assertEqual(declaration.status, DividendDeclaration.Status.CALCULATED)
        self.assertIsNotNone(declaration.calculated_at)
        self.assertTrue(
            SystemAuditLog.objects.filter(
                user=self.admin,
                action='DIVIDEND_CALCULATED',
                resource_type='DividendDeclaration',
                resource_id=str(declaration.id),
            ).exists()
        )

    def test_approve_dividend_declaration(self):
        declaration = DividendDeclaration.objects.create(
            sacco=self.sacco,
            savings_type=self.savings_type,
            financial_year='2025/2026',
            declared_rate=Decimal('10.00'),
            period_start=date(2025, 1, 1),
            period_end=date(2025, 12, 31),
            status=DividendDeclaration.Status.CALCULATED,
        )
        
        response = self.client.post(
            f'/api/v1/services/dividends/declarations/{declaration.id}/approve/',
            HTTP_X_SACCO_ID=str(self.sacco.id),
        )
        
        self.assertEqual(response.status_code, 200)
        declaration.refresh_from_db()
        self.assertEqual(declaration.status, DividendDeclaration.Status.APPROVED)
        self.assertEqual(declaration.approved_by, self.admin)
        self.assertTrue(
            SystemAuditLog.objects.filter(
                user=self.admin,
                action='DIVIDEND_APPROVED',
                resource_type='DividendDeclaration',
                resource_id=str(declaration.id),
            ).exists()
        )

    def test_approve_requires_calculated_status(self):
        declaration = DividendDeclaration.objects.create(
            sacco=self.sacco,
            savings_type=self.savings_type,
            financial_year='2025/2026',
            declared_rate=Decimal('10.00'),
            period_start=date(2025, 1, 1),
            period_end=date(2025, 12, 31),
            status=DividendDeclaration.Status.DRAFT,
        )
        
        response = self.client.post(
            f'/api/v1/services/dividends/declarations/{declaration.id}/approve/',
            HTTP_X_SACCO_ID=str(self.sacco.id),
        )
        
        self.assertEqual(response.status_code, 400)

    def test_disburse_dividends_creates_ledger_entries(self):
        # Create member with saving
        member = User.objects.create_user(
            email='dividend-member@example.com',
            password='secret',
            phone_number='254712345671',
        )
        membership = Membership.objects.create(
            user=member,
            sacco=self.sacco,
            status=Membership.Status.APPROVED,
            member_number='DIV-M003',
        )
        saving = Saving.objects.create(
            membership=membership,
            savings_type=self.savings_type,
            amount=Decimal('10000.00'),
            status=Saving.Status.ACTIVE,
            dividend_eligible=True,
        )
        
        declaration = DividendDeclaration.objects.create(
            sacco=self.sacco,
            savings_type=self.savings_type,
            financial_year='2025/2026',
            declared_rate=Decimal('10.00'),
            period_start=date(2025, 1, 1),
            period_end=date(2025, 12, 31),
            status=DividendDeclaration.Status.APPROVED,
        )
        
        # Create payout
        payout = DividendPayout.objects.create(
            declaration=declaration,
            membership=membership,
            saving=saving,
            average_balance=Decimal('10000.00'),
            dividend_amount=Decimal('1000.00'),
            status=DividendPayout.Status.PENDING,
        )
        
        initial_balance = saving.amount
        
        response = self.client.post(
            f'/api/v1/services/dividends/declarations/{declaration.id}/disburse/',
            HTTP_X_SACCO_ID=str(self.sacco.id),
        )
        
        self.assertEqual(response.status_code, 200)
        declaration.refresh_from_db()
        self.assertEqual(declaration.status, DividendDeclaration.Status.DISBURSED)
        
        payout.refresh_from_db()
        self.assertEqual(payout.status, DividendPayout.Status.PAID)
        
        saving.refresh_from_db()
        self.assertEqual(
            saving.amount,
            initial_balance + payout.dividend_amount,
        )
        self.assertTrue(
            SystemAuditLog.objects.filter(
                user=self.admin,
                action='DIVIDEND_DISBURSED',
                resource_type='DividendDeclaration',
                resource_id=str(declaration.id),
            ).exists()
        )

    def test_disburse_requires_approved_status(self):
        declaration = DividendDeclaration.objects.create(
            sacco=self.sacco,
            savings_type=self.savings_type,
            financial_year='2025/2026',
            declared_rate=Decimal('10.00'),
            period_start=date(2025, 1, 1),
            period_end=date(2025, 12, 31),
            status=DividendDeclaration.Status.CALCULATED,
        )
        
        response = self.client.post(
            f'/api/v1/services/dividends/declarations/{declaration.id}/disburse/',
            HTTP_X_SACCO_ID=str(self.sacco.id),
        )
        
        self.assertEqual(response.status_code, 400)

    def test_list_payouts_filterable_by_declaration(self):
        declaration1 = DividendDeclaration.objects.create(
            sacco=self.sacco,
            savings_type=self.savings_type,
            financial_year='2024/2025',
            declared_rate=Decimal('10.00'),
            period_start=date(2024, 1, 1),
            period_end=date(2024, 12, 31),
            status=DividendDeclaration.Status.APPROVED,
        )
        declaration2 = DividendDeclaration.objects.create(
            sacco=self.sacco,
            savings_type=self.savings_type,
            financial_year='2025/2026',
            declared_rate=Decimal('12.00'),
            period_start=date(2025, 1, 1),
            period_end=date(2025, 12, 31),
            status=DividendDeclaration.Status.APPROVED,
        )
        
        response = self.client.get(
            '/api/v1/services/dividends/payouts/',
            {'declaration': str(declaration1.id)},
            HTTP_X_SACCO_ID=str(self.sacco.id),
        )
        
        self.assertEqual(response.status_code, 200)


class DuplicateDividendDeclarationTests(TestCase):
    """One dividend declaration per (sacco, savings_type, financial_year)."""

    FY = '2025/2026'

    def setUp(self):
        self.client = APIClient()
        self.sacco = Sacco.objects.create(
            name='Dup Dividend SACCO A',
            registration_number='DUPDIV-A',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
        )
        self.savings_type = SavingsType.objects.create(
            sacco=self.sacco,
            name=SavingsType.Name.BOSA,
            minimum_contribution=Decimal('500.00'),
        )
        self.admin = User.objects.create_user(
            email='dup-dividend-admin@example.com',
            password='secret',
            phone_number='254712345691',
        )
        Role.objects.create(
            user=self.admin, sacco=self.sacco, name=Role.SACCO_ADMIN,
        )
        self.client.force_authenticate(user=self.admin)

        self.existing = DividendDeclaration.objects.create(
            sacco=self.sacco,
            savings_type=self.savings_type,
            financial_year=self.FY,
            declared_rate=Decimal('10.00'),
            period_start=date(2025, 1, 1),
            period_end=date(2025, 12, 31),
            status=DividendDeclaration.Status.DRAFT,
        )

    def _post(self, sacco, savings_type, financial_year, **overrides):
        body = {
            'savings_type': str(savings_type.id),
            'financial_year': financial_year,
            'declared_rate': '12.50',
            'period_start': '2025-01-01',
            'period_end': '2025-12-31',
        }
        body.update(overrides)
        return self.client.post(
            '/api/v1/services/dividends/declarations/',
            body,
            format='json',
            HTTP_X_SACCO_ID=str(sacco.id),
        )

    def test_api_rejects_duplicate_with_400(self):
        response = self._post(self.sacco, self.savings_type, self.FY)
        self.assertEqual(response.status_code, 400)
        body = response.json()
        errors = body.get('errors', body)
        self.assertIn('financial_year', errors)
        self.assertEqual(
            DividendDeclaration.objects.filter(
                sacco=self.sacco,
                savings_type=self.savings_type,
                financial_year=self.FY,
            ).count(),
            1,
        )

    def test_api_allows_a_different_financial_year(self):
        response = self._post(self.sacco, self.savings_type, '2026/2027')
        self.assertEqual(response.status_code, 201)

    def test_direct_db_insert_of_duplicate_raises_integrity_error(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                DividendDeclaration.objects.create(
                    sacco=self.sacco,
                    savings_type=self.savings_type,
                    financial_year=self.FY,
                    declared_rate=Decimal('9.00'),
                    period_start=date(2025, 1, 1),
                    period_end=date(2025, 12, 31),
                    status=DividendDeclaration.Status.DRAFT,
                )

    def test_same_type_and_year_allowed_for_a_different_sacco(self):
        other_sacco = Sacco.objects.create(
            name='Dup Dividend SACCO B',
            registration_number='DUPDIV-B',
            sector=Sacco.Sector.FINANCE,
            county='Kiambu',
        )
        other_type = SavingsType.objects.create(
            sacco=other_sacco,
            name=SavingsType.Name.BOSA,
            minimum_contribution=Decimal('500.00'),
        )
        Role.objects.create(
            user=self.admin, sacco=other_sacco, name=Role.SACCO_ADMIN,
        )

        # Direct insert: no cross-SACCO clash.
        DividendDeclaration.objects.create(
            sacco=other_sacco,
            savings_type=other_type,
            financial_year=self.FY,
            declared_rate=Decimal('11.00'),
            period_start=date(2025, 1, 1),
            period_end=date(2025, 12, 31),
            status=DividendDeclaration.Status.DRAFT,
        )

        # And via the API for that other tenant.
        DividendDeclaration.objects.filter(
            sacco=other_sacco,
        ).delete()
        response = self._post(other_sacco, other_type, self.FY)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(
            DividendDeclaration.objects.filter(
                savings_type=other_type, financial_year=self.FY,
            ).count(),
            1,
        )

    def test_editing_the_declaration_in_place_is_not_a_duplicate(self):
        response = self.client.patch(
            f'/api/v1/services/dividends/declarations/{self.existing.id}/',
            {'declared_rate': '13.00'},
            format='json',
            HTTP_X_SACCO_ID=str(self.sacco.id),
        )
        self.assertEqual(response.status_code, 200)
        self.existing.refresh_from_db()
        self.assertEqual(self.existing.declared_rate, Decimal('13.00'))

    def test_audit_command_is_clean_when_there_are_no_duplicates(self):
        # Distinct declarations (different years) must NOT be flagged.
        DividendDeclaration.objects.create(
            sacco=self.sacco,
            savings_type=self.savings_type,
            financial_year='2026/2027',
            declared_rate=Decimal('9.00'),
            period_start=date(2026, 1, 1),
            period_end=date(2026, 12, 31),
            status=DividendDeclaration.Status.DRAFT,
        )
        out = StringIO()
        call_command('audit_duplicate_dividend_declarations', stdout=out)
        self.assertIn('No duplicate dividend declarations', out.getvalue())


class DuplicateDividendAuditCommandTests(TransactionTestCase):
    """The pre-migration audit command surfaces real duplicate rows.

    Planting duplicates needs the unique constraint dropped first. That is
    a clean ``ALTER TABLE ... DROP CONSTRAINT`` on PostgreSQL but a full
    table rebuild on SQLite (which re-adds the constraint from model
    state), so this runs on PostgreSQL only - mirroring the other
    DB-behaviour tests in this project. The clean path is covered on every
    backend by
    DividendDeclarationAPITests-side
    test_audit_command_is_clean_when_there_are_no_duplicates.
    """

    def setUp(self):
        if connection.vendor != 'postgresql':
            self.skipTest(
                'Dropping a UniqueConstraint to plant duplicates only '
                'works cleanly on PostgreSQL; SQLite rebuilds the table '
                'and re-applies the constraint from model state.'
            )
        self.sacco = Sacco.objects.create(
            name='Audit Cmd SACCO',
            registration_number='AUDCMD-1',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
        )
        self.savings_type = SavingsType.objects.create(
            sacco=self.sacco,
            name=SavingsType.Name.BOSA,
            minimum_contribution=Decimal('500.00'),
        )
        self._constraint = next(
            c for c in DividendDeclaration._meta.constraints
            if c.name == 'unique_dividend_declaration_per_sacco_type_year'
        )

    def _make(self, financial_year, rate):
        return DividendDeclaration.objects.create(
            sacco=self.sacco,
            savings_type=self.savings_type,
            financial_year=financial_year,
            declared_rate=Decimal(rate),
            period_start=date(2025, 1, 1),
            period_end=date(2025, 12, 31),
            status=DividendDeclaration.Status.DRAFT,
        )

    def test_reports_duplicates_and_raises_command_error(self):
        with connection.schema_editor(atomic=False) as editor:
            editor.remove_constraint(DividendDeclaration, self._constraint)
        try:
            self._make('2025/2026', '10.00')
            self._make('2025/2026', '11.00')

            out = StringIO()
            with self.assertRaises(CommandError):
                call_command(
                    'audit_duplicate_dividend_declarations', stdout=out,
                )
            self.assertIn(
                'duplicate dividend declaration group', out.getvalue(),
            )
            self.assertIn(str(self.sacco.id), out.getvalue())
        finally:
            DividendDeclaration.objects.all().delete()
            with connection.schema_editor(atomic=False) as editor:
                editor.add_constraint(DividendDeclaration, self._constraint)
