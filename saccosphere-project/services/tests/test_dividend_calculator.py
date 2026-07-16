"""Tests for dividend calculation engine and views."""

from decimal import Decimal
from datetime import date, timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import Sacco, SaccoSettings, User
from saccomanagement.models import Role
from saccomembership.models import Membership
from services.engines.dividend_calculator import (
    calculate_average_balance,
    calculate_dividends_for_declaration,
)
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
        
        # Should return a Decimal
        self.assertIsInstance(average, Decimal)
        # Should be rounded to 2 decimal places
        self.assertEqual(
            average.as_tuple().exponent,
            -2,
            'Average should be rounded to 2 decimal places',
        )

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
