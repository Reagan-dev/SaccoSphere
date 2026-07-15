from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import Sacco, SaccoSettings, User
from ledger.models import LedgerEntry
from notifications.models import Notification
from saccomanagement.models import Role
from saccomembership.models import Membership
from services.engines.liquidity_monitor import check_liquidity_risk
from services.models import LiquidityAlert, Loan
from services.tasks import check_all_sacco_liquidity


class LiquidityMonitorTests(TestCase):
    def setUp(self):
        self.sacco = Sacco.objects.create(
            name='Liquidity SACCO',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
        )
        SaccoSettings.objects.create(
            sacco=self.sacco,
            liquidity_threshold_percentage=Decimal('80.00'),
        )
        self.admin = User.objects.create_user(
            email='admin@example.com',
            password='secret',
            first_name='Sacco',
            last_name='Admin',
            phone_number='254712345678',
        )
        Role.objects.create(
            user=self.admin,
            sacco=self.sacco,
            name=Role.SACCO_ADMIN,
        )
        self.member = User.objects.create_user(
            email='member@example.com',
            password='secret',
            first_name='Sacco',
            last_name='Member',
        )
        self.membership = Membership.objects.create(
            user=self.member,
            sacco=self.sacco,
            status=Membership.Status.APPROVED,
            member_number='MEM-001',
        )

    def test_check_liquidity_risk_uses_decimal_cash_categories(self):
        self._ledger(
            LedgerEntry.EntryType.CREDIT,
            LedgerEntry.Category.SAVING_DEPOSIT,
            Decimal('1000.00'),
            'DEP-001',
        )
        self._ledger(
            LedgerEntry.EntryType.CREDIT,
            LedgerEntry.Category.FEE,
            Decimal('200.00'),
            'FEE-001',
        )
        self._ledger(
            LedgerEntry.EntryType.DEBIT,
            LedgerEntry.Category.SAVING_WITHDRAWAL,
            Decimal('100.00'),
            'WDR-001',
        )
        self._ledger(
            LedgerEntry.EntryType.DEBIT,
            LedgerEntry.Category.LOAN_DISBURSEMENT,
            Decimal('300.00'),
            'DIS-001',
        )
        self._loan(Decimal('600.00'), Loan.Status.APPROVED)
        self._loan(Decimal('100.00'), Loan.Status.DISBURSEMENT_PENDING)
        self._loan(Decimal('900.00'), Loan.Status.REJECTED)

        risk = check_liquidity_risk(self.sacco)

        self.assertEqual(risk['available_reserves'], Decimal('800.00'))
        self.assertEqual(
            risk['pending_disbursements'],
            Decimal('700.00'),
        )
        self.assertEqual(risk['utilisation_pct'], Decimal('87.50'))
        self.assertTrue(risk['at_risk'])

    @patch('services.tasks.send_sms_notification')
    def test_task_creates_one_recent_alert_and_notifies_admin(self, sms_mock):
        self._ledger(
            LedgerEntry.EntryType.CREDIT,
            LedgerEntry.Category.SAVING_DEPOSIT,
            Decimal('100.00'),
            'DEP-002',
        )
        self._loan(Decimal('100.00'), Loan.Status.APPROVED)

        result = check_all_sacco_liquidity()
        check_all_sacco_liquidity()

        self.assertEqual(result['checked'], 1)
        self.assertEqual(LiquidityAlert.objects.count(), 1)
        self.assertEqual(Notification.objects.count(), 1)
        self.assertEqual(
            Notification.objects.get().category,
            Notification.Category.LIQUIDITY_WARNING,
        )
        sms_mock.assert_called_once()

    def test_task_resolves_alert_when_pending_disbursements_drop(self):
        self._ledger(
            LedgerEntry.EntryType.CREDIT,
            LedgerEntry.Category.SAVING_DEPOSIT,
            Decimal('1000.00'),
            'DEP-003',
        )
        loan = self._loan(Decimal('900.00'), Loan.Status.APPROVED)

        check_all_sacco_liquidity()
        alert = LiquidityAlert.objects.get()
        self.assertFalse(alert.resolved)

        loan.status = Loan.Status.DISBURSED
        loan.save(update_fields=['status', 'updated_at'])
        result = check_all_sacco_liquidity()

        alert.refresh_from_db()
        self.assertEqual(result['alerts_resolved'], 1)
        self.assertTrue(alert.resolved)
        self.assertIsNotNone(alert.resolved_at)

    def test_management_liquidity_endpoint_returns_current_numbers(self):
        self._ledger(
            LedgerEntry.EntryType.CREDIT,
            LedgerEntry.Category.SAVING_DEPOSIT,
            Decimal('1000.00'),
            'DEP-004',
        )
        self._ledger(
            LedgerEntry.EntryType.DEBIT,
            LedgerEntry.Category.SAVING_WITHDRAWAL,
            Decimal('100.00'),
            'WDR-004',
        )
        self._loan(Decimal('720.00'), Loan.Status.APPROVED)

        client = APIClient()
        client.force_authenticate(user=self.admin)
        response = client.get('/api/v1/management/liquidity/')

        self.assertEqual(response.status_code, 200)
        data = response.json()['data']
        self.assertEqual(data['sacco_id'], str(self.sacco.id))
        self.assertEqual(data['current']['available_reserves'], '900.00')
        self.assertEqual(
            data['current']['pending_disbursements'],
            '720.00',
        )
        self.assertEqual(data['current']['utilisation_pct'], '80.00')
        self.assertTrue(data['current']['at_risk'])
        self.assertEqual(data['recent_alerts'], [])

    def _ledger(self, entry_type, category, amount, reference):
        return LedgerEntry.objects.create(
            membership=self.membership,
            entry_type=entry_type,
            category=category,
            amount=amount,
            reference=reference,
            description=reference,
            balance_after=Decimal('0.00'),
        )

    def _loan(self, amount, status):
        return Loan.objects.create(
            membership=self.membership,
            amount=amount,
            interest_rate=Decimal('12.00'),
            term_months=12,
            status=status,
        )
