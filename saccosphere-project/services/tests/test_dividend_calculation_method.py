"""Per-SACCO dividend calculation method (strategy dispatch).

- ``SaccoSettings.dividend_calculation_method`` selects the balance
  basis; ``AVERAGE_MONTH_END`` is the default and the only one with a
  real engine.
- The dispatcher never falls back: an unimplemented / unknown method
  raises ``DividendMethodNotSupported`` (a ``NotImplementedError``).
- ``DividendCalculateView`` refuses an unsupported method with a
  synchronous 400 and queues nothing.
- ``SaccoSettingsSerializer`` refuses to save an unsupported method.
"""

from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import Sacco, SaccoSettings, User
from saccomanagement.models import Role
from saccomembership.models import Membership
from services.engines.dividend_calculator import (
    DividendMethodNotSupported,
    calculate_average_balance,
    calculate_dividends_for_declaration,
    calculate_period_balance,
    is_supported_dividend_method,
    resolve_dividend_calculation_method,
)
from services.models import DividendDeclaration, DividendPayout, Saving, SavingsType


_METHOD = SaccoSettings.DividendCalculationMethod


class _DividendMethodFixture(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.sacco = Sacco.objects.create(
            name='Method SACCO',
            registration_number='DMETH-1',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
        )
        self.savings_type = SavingsType.objects.create(
            sacco=self.sacco,
            name=SavingsType.Name.BOSA,
            minimum_contribution=Decimal('100.00'),
        )
        self.admin = User.objects.create_user(
            email='dmeth-admin@example.com', password='StrongPass1',
        )
        Role.objects.create(
            user=self.admin, sacco=self.sacco, name=Role.SACCO_ADMIN,
        )
        self.client.force_authenticate(self.admin)

    def _settings(self, method):
        obj, _ = SaccoSettings.objects.get_or_create(sacco=self.sacco)
        # Set directly - bypasses the serializer guard on purpose, to
        # model a value that reached the row via the Django admin or a
        # future data migration.
        SaccoSettings.objects.filter(pk=obj.pk).update(
            dividend_calculation_method=method,
        )
        obj.refresh_from_db()
        return obj

    def _eligible_member(self, i, amount):
        user = User.objects.create_user(
            email=f'dmeth-m{i}@example.com', password='StrongPass1',
        )
        membership = Membership.objects.create(
            user=user,
            sacco=self.sacco,
            status=Membership.Status.APPROVED,
            member_number=f'DMETH-M{i:03d}',
        )
        return Saving.objects.create(
            membership=membership,
            savings_type=self.savings_type,
            amount=Decimal(amount),
            status=Saving.Status.ACTIVE,
            dividend_eligible=True,
        )

    def _declaration(self, status=DividendDeclaration.Status.DRAFT):
        return DividendDeclaration.objects.create(
            sacco=self.sacco,
            savings_type=self.savings_type,
            financial_year='2025/2026',
            declared_rate=Decimal('10.00'),
            period_start=date(2025, 1, 1),
            period_end=date(2025, 12, 31),
            status=status,
        )

    def _calc_url(self, declaration):
        return (
            f'/api/v1/services/dividends/declarations/'
            f'{declaration.id}/calculate/'
        )


class DispatchTests(_DividendMethodFixture):
    def test_resolve_defaults_to_average_month_end_without_settings(self):
        self.assertFalse(hasattr(self.sacco, 'settings'))
        self.assertEqual(
            resolve_dividend_calculation_method(self.sacco),
            _METHOD.AVERAGE_MONTH_END,
        )

    def test_only_average_month_end_is_supported(self):
        self.assertTrue(
            is_supported_dividend_method(_METHOD.AVERAGE_MONTH_END)
        )
        self.assertFalse(is_supported_dividend_method(_METHOD.DAY_WEIGHTED))
        self.assertFalse(is_supported_dividend_method(_METHOD.MINIMUM_BALANCE))
        self.assertFalse(is_supported_dividend_method('SOMETHING_ELSE'))

    def test_average_month_end_dispatch_matches_direct_call(self):
        saving = self._eligible_member(1, '10000.00')
        direct = calculate_average_balance(
            saving, date(2025, 1, 1), date(2025, 12, 31),
        )
        dispatched = calculate_period_balance(
            saving, date(2025, 1, 1), date(2025, 12, 31),
            method=_METHOD.AVERAGE_MONTH_END,
        )
        self.assertEqual(direct, dispatched)

    def test_stub_methods_raise_not_supported(self):
        saving = self._eligible_member(2, '10000.00')
        for method in (_METHOD.DAY_WEIGHTED, _METHOD.MINIMUM_BALANCE):
            with self.subTest(method=method):
                with self.assertRaises(DividendMethodNotSupported) as ctx:
                    calculate_period_balance(
                        saving, date(2025, 1, 1), date(2025, 12, 31),
                        method=method,
                    )
                # A NotImplementedError subclass, and it names the method.
                self.assertIsInstance(ctx.exception, NotImplementedError)
                self.assertEqual(ctx.exception.method, method)

    def test_unknown_method_raises_not_supported(self):
        saving = self._eligible_member(3, '10000.00')
        with self.assertRaises(DividendMethodNotSupported):
            calculate_period_balance(
                saving, date(2025, 1, 1), date(2025, 12, 31),
                method='NONSENSE',
            )


class EngineBackstopTests(_DividendMethodFixture):
    def test_default_method_calculates_normally(self):
        self._eligible_member(1, '10000.00')
        self._eligible_member(2, '20000.00')
        declaration = self._declaration()

        result = calculate_dividends_for_declaration(declaration)

        declaration.refresh_from_db()
        self.assertEqual(
            declaration.status, DividendDeclaration.Status.CALCULATED,
        )
        self.assertEqual(result['payout_count'], 2)
        self.assertEqual(DividendPayout.objects.count(), 2)

    def test_explicit_average_month_end_is_unchanged(self):
        self._settings(_METHOD.AVERAGE_MONTH_END)
        self._eligible_member(1, '10000.00')
        declaration = self._declaration()

        result = calculate_dividends_for_declaration(declaration)

        self.assertEqual(result['payout_count'], 1)

    def test_unsupported_method_raises_and_writes_no_payouts(self):
        self._settings(_METHOD.MINIMUM_BALANCE)
        self._eligible_member(1, '10000.00')
        declaration = self._declaration()

        with self.assertRaises(DividendMethodNotSupported):
            calculate_dividends_for_declaration(declaration)

        declaration.refresh_from_db()
        # Backstop bailed before any payout write and before flipping
        # the status to CALCULATED - no silent AVERAGE_MONTH_END run.
        self.assertEqual(declaration.payouts.count(), 0)
        self.assertNotEqual(
            declaration.status, DividendDeclaration.Status.CALCULATED,
        )


class CalculateViewMethodGuardTests(_DividendMethodFixture):
    def test_default_method_returns_202(self):
        declaration = self._declaration()

        with patch(
            'services.tasks.calculate_dividends_for_declaration_task.delay',
        ) as delayed, self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                self._calc_url(declaration),
                HTTP_X_SACCO_ID=str(self.sacco.id),
            )

        self.assertEqual(response.status_code, 202)
        delayed.assert_called_once()

    def test_unsupported_method_returns_400_and_queues_nothing(self):
        self._settings(_METHOD.DAY_WEIGHTED)
        declaration = self._declaration()

        with patch(
            'services.tasks.calculate_dividends_for_declaration_task.delay',
        ) as delayed, self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                self._calc_url(declaration),
                HTTP_X_SACCO_ID=str(self.sacco.id),
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn('not yet supported', response.json()['detail'])
        self.assertIn('DAY_WEIGHTED', response.json()['detail'])
        delayed.assert_not_called()

        declaration.refresh_from_db()
        # Status untouched - not moved to CALCULATING.
        self.assertEqual(
            declaration.status, DividendDeclaration.Status.DRAFT,
        )


class SettingsSerializerMethodGuardTests(_DividendMethodFixture):
    URL = '/api/v1/management/settings/'

    def setUp(self):
        super().setUp()
        # self.admin is a SACCO_ADMIN for self.sacco -> the settings
        # endpoint resolves to that SACCO with no extra params.
        SaccoSettings.objects.get_or_create(sacco=self.sacco)

    def test_patch_to_average_month_end_is_accepted(self):
        response = self.client.patch(
            self.URL,
            {'dividend_calculation_method': _METHOD.AVERAGE_MONTH_END},
            format='json',
        )
        self.assertEqual(response.status_code, 200)

    def test_patch_to_unsupported_method_is_rejected(self):
        response = self.client.patch(
            self.URL,
            {'dividend_calculation_method': _METHOD.DAY_WEIGHTED},
            format='json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn(
            'dividend_calculation_method',
            str(response.json()),
        )
        self.sacco.settings.refresh_from_db()
        self.assertEqual(
            self.sacco.settings.dividend_calculation_method,
            _METHOD.AVERAGE_MONTH_END,
        )
