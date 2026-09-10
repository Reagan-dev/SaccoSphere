"""Config-field validation: annual-rate bounds + canonical financial_year.

A. DividendDeclaration.declared_rate / SavingsType.interest_rate reject
   negative and above-ceiling values (ceiling is a placeholder - 100%).
B. DividendDeclaration.financial_year must be canonical YYYY/YYYY with
   consecutive years, enforced in the serializer, the model (admin), and
   surfaced by the audit command; existing valid data is untouched.
"""

from datetime import date
from decimal import Decimal
from io import StringIO

from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import Sacco, User
from saccomanagement.models import Role
from services.models import DividendDeclaration, SavingsType
from services.validators import (
    MAX_ANNUAL_RATE_PERCENT,
    is_canonical_financial_year,
    validate_financial_year,
)


class _ApiFixture(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.sacco = Sacco.objects.create(
            name='Config Val SACCO',
            registration_number='CFGV-1',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
        )
        self.savings_type = SavingsType.objects.create(
            sacco=self.sacco,
            name=SavingsType.Name.BOSA,
            minimum_contribution=Decimal('100.00'),
        )
        self.admin = User.objects.create_user(
            email='cfgv-admin@example.com', password='StrongPass1',
        )
        Role.objects.create(
            user=self.admin, sacco=self.sacco, name=Role.SACCO_ADMIN,
        )
        self.client.force_authenticate(user=self.admin)

    def _post_declaration(self, **overrides):
        body = {
            'savings_type': str(self.savings_type.id),
            'financial_year': '2025/2026',
            'declared_rate': '10.00',
            'period_start': '2025-01-01',
            'period_end': '2025-12-31',
        }
        body.update(overrides)
        return self.client.post(
            '/api/v1/services/dividends/declarations/',
            body,
            format='json',
            HTTP_X_SACCO_ID=str(self.sacco.id),
        )

    def _post_savings_type(self, **overrides):
        body = {
            'name': SavingsType.Name.FOSA,
            'minimum_contribution': '500.00',
        }
        body.update(overrides)
        return self.client.post(
            '/api/v1/services/savings-types/',
            body,
            format='json',
            HTTP_X_SACCO_ID=str(self.sacco.id),
        )


class AnnualRateBoundsTests(_ApiFixture):
    def test_declared_rate_negative_is_rejected(self):
        response = self._post_declaration(declared_rate='-1.00')
        self.assertEqual(response.status_code, 400)
        self.assertIn('negative', str(response.json()).lower())
        self.assertFalse(DividendDeclaration.objects.exists())

    def test_declared_rate_above_ceiling_is_rejected_with_the_ceiling(self):
        response = self._post_declaration(declared_rate='150.00')
        self.assertEqual(response.status_code, 400)
        body = str(response.json()).lower()
        self.assertIn('exceed', body)
        self.assertIn('100', body)
        self.assertFalse(DividendDeclaration.objects.exists())

    def test_declared_rate_within_bounds_is_accepted(self):
        response = self._post_declaration(declared_rate='12.50')
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(
            DividendDeclaration.objects.get().declared_rate,
            Decimal('12.50'),
        )

    def test_savings_type_interest_rate_negative_is_rejected(self):
        response = self._post_savings_type(interest_rate='-5.00')
        self.assertEqual(response.status_code, 400)
        self.assertIn('negative', str(response.json()).lower())

    def test_savings_type_interest_rate_above_ceiling_is_rejected(self):
        response = self._post_savings_type(interest_rate='250.00')
        self.assertEqual(response.status_code, 400)
        self.assertIn('exceed', str(response.json()).lower())

    def test_savings_type_interest_rate_within_bounds_is_accepted(self):
        response = self._post_savings_type(interest_rate='7.50')
        self.assertEqual(response.status_code, 201, response.content)

    def test_model_full_clean_enforces_rate_bounds(self):
        declaration = DividendDeclaration(
            sacco=self.sacco,
            savings_type=self.savings_type,
            financial_year='2030/2031',
            declared_rate=Decimal('200.00'),
            period_start=date(2030, 1, 1),
            period_end=date(2030, 12, 31),
        )
        with self.assertRaises(DjangoValidationError) as ctx:
            declaration.full_clean()
        self.assertIn('declared_rate', ctx.exception.message_dict)

        savings_type = SavingsType(
            sacco=self.sacco,
            name=SavingsType.Name.SHARE_CAPITAL,
            minimum_contribution=Decimal('0.00'),
            interest_rate=Decimal('-1.00'),
        )
        with self.assertRaises(DjangoValidationError) as ctx:
            savings_type.full_clean()
        self.assertIn('interest_rate', ctx.exception.message_dict)

    def test_placeholder_ceiling_is_one_hundred(self):
        # Guards against the placeholder silently drifting before sign-off.
        self.assertEqual(MAX_ANNUAL_RATE_PERCENT, Decimal('100.00'))


class FinancialYearFormatTests(_ApiFixture):
    def test_validator_accepts_the_canonical_format(self):
        for good in ('2025/2026', '2000/2001', '2099/2100'):
            validate_financial_year(good)  # must not raise
            self.assertTrue(is_canonical_financial_year(good))

    def test_validator_rejects_the_bad_examples(self):
        bad_values = [
            'FY25',
            'garbage',
            '2025',
            '2025/2027',   # not consecutive
            '2025-2026',
            '25/26',
            '2025/2026 ',
            '',
        ]
        for bad in bad_values:
            with self.subTest(value=bad):
                with self.assertRaises(DjangoValidationError):
                    validate_financial_year(bad)
                self.assertFalse(is_canonical_financial_year(bad))

    def test_api_rejects_non_canonical_financial_year(self):
        for bad in ('FY25', 'garbage', '2025', '2025/2027'):
            with self.subTest(value=bad):
                response = self._post_declaration(financial_year=bad)
                self.assertEqual(response.status_code, 400)
                self.assertIn(
                    'financial_year', str(response.json()).lower(),
                )
        self.assertFalse(DividendDeclaration.objects.exists())

    def test_api_accepts_canonical_financial_year(self):
        response = self._post_declaration(financial_year='2026/2027')
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(
            DividendDeclaration.objects.get().financial_year, '2026/2027',
        )

    def test_model_full_clean_enforces_financial_year(self):
        declaration = DividendDeclaration(
            sacco=self.sacco,
            savings_type=self.savings_type,
            financial_year='FY25',
            declared_rate=Decimal('10.00'),
            period_start=date(2025, 1, 1),
            period_end=date(2025, 12, 31),
        )
        with self.assertRaises(DjangoValidationError) as ctx:
            declaration.full_clean()
        self.assertIn('financial_year', ctx.exception.message_dict)


class FinancialYearAuditCommandTests(_ApiFixture):
    def _make(self, financial_year):
        return DividendDeclaration.objects.create(
            sacco=self.sacco,
            savings_type=self.savings_type,
            financial_year=financial_year,
            declared_rate=Decimal('10.00'),
            period_start=date(2025, 1, 1),
            period_end=date(2025, 12, 31),
        )

    def test_clean_data_passes_the_audit(self):
        self._make('2025/2026')
        out = StringIO()
        call_command('audit_financial_year_format', stdout=out)
        self.assertIn('canonical', out.getvalue())

    def test_audit_reports_and_fails_on_non_canonical_rows(self):
        declaration = self._make('2025/2026')
        # Plant a bad value bypassing validation (simulates legacy data).
        DividendDeclaration.objects.filter(pk=declaration.pk).update(
            financial_year='FY25',
        )

        out = StringIO()
        with self.assertRaises(CommandError):
            call_command('audit_financial_year_format', stdout=out)
        report = out.getvalue()
        self.assertIn('FY25', report)
        self.assertIn(str(self.sacco.id), report)

    def test_audit_can_be_scoped_to_one_sacco(self):
        other = Sacco.objects.create(
            name='Other Audit SACCO',
            registration_number='CFGV-2',
            sector=Sacco.Sector.FINANCE,
            county='Kiambu',
        )
        other_type = SavingsType.objects.create(
            sacco=other,
            name=SavingsType.Name.BOSA,
            minimum_contribution=Decimal('100.00'),
        )
        bad = DividendDeclaration.objects.create(
            sacco=other,
            savings_type=other_type,
            financial_year='2025/2026',
            declared_rate=Decimal('10.00'),
            period_start=date(2025, 1, 1),
            period_end=date(2025, 12, 31),
        )
        DividendDeclaration.objects.filter(pk=bad.pk).update(
            financial_year='2025',
        )
        self._make('2025/2026')  # this SACCO is clean

        out = StringIO()
        call_command(
            'audit_financial_year_format',
            '--sacco', str(self.sacco.id),
            stdout=out,
        )
        self.assertIn('canonical', out.getvalue())
