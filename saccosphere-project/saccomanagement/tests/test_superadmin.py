"""Tests for Super Admin dashboard views."""

from decimal import Decimal
from uuid import uuid4

from django.test import TestCase
from django.utils import timezone

from rest_framework.test import APITestCase, APIClient
from rest_framework import status

from accounts.models import User, Sacco
from saccomembership.models import Membership
from saccomanagement.models import ComplianceFlag, Role
from billing.models import InvoiceLineItem, PlatformRevenue
from payments.models import Transaction, MpesaTransaction, PaymentProvider


def _create_invoice_line_item(sacco, platform_fee, transaction_type='deposit'):
    """Build a minimal, valid InvoiceLineItem for dashboard revenue tests."""
    provider, _ = PaymentProvider.objects.get_or_create(
        name='M-Pesa',
        defaults={
            'provider_type': PaymentProvider.ProviderType.MPESA,
            'is_active': True,
        },
    )
    transaction = Transaction.objects.create(
        provider=provider,
        user=User.objects.create_user(
            email=f'line-item-{uuid4().hex[:12]}@example.com',
            password='testpass123',
        ),
        reference=f'DASH-TXN-{uuid4().hex[:12]}',
        transaction_type=Transaction.TransactionType.DEPOSIT,
        amount=Decimal('1000.00'),
        status=Transaction.Status.COMPLETED,
        sacco=sacco,
    )
    return InvoiceLineItem.objects.create(
        sacco=sacco,
        transaction=transaction,
        transaction_type=transaction_type,
        gross_amount=Decimal('1000.00') + platform_fee,
        net_amount=Decimal('1000.00'),
        platform_fee=platform_fee,
        fee_model='percentage',
        rate_applied='1.0% of deposit amount',
        billing_month=timezone.localdate().replace(day=1),
        invoiced=False,
    )


class SystemOverviewTest(APITestCase):
    """Test SystemOverviewView."""

    def setUp(self):
        """Set up test data."""
        self.client = APIClient()

        # Create super admin
        self.super_admin = User.objects.create_user(
            email='superadmin@saccosphere.com',
            password='testpass123',
            first_name='Super',
            last_name='Admin',
        )
        Role.objects.create(
            user=self.super_admin,
            name=Role.SUPER_ADMIN,
            sacco=None,
        )

        # Create SACCO admin
        self.sacco = Sacco.objects.create(
            name='Test SACCO',
            county='Nairobi',
            sector='EDUCATION',
        )
        self.sacco_admin = User.objects.create_user(
            email='admin@test.com',
            password='testpass123',
            first_name='Sacco',
            last_name='Admin',
        )
        Role.objects.create(
            user=self.sacco_admin,
            name=Role.SACCO_ADMIN,
            sacco=self.sacco,
        )

    def test_super_admin_can_access_overview(self):
        """Super admin can access overview endpoint."""
        self.client.force_authenticate(user=self.super_admin)
        response = self.client.get('/api/v1/management/superadmin/overview/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('platform_transaction_volume_mtd', response.data)
        self.assertIn('platform_transaction_volume_change_pct', response.data)
        self.assertIn('active_saccos_count', response.data)
        self.assertIn('active_saccos_change_this_month', response.data)
        self.assertIn('total_members', response.data)
        self.assertIn('total_members_change_this_month', response.data)
        self.assertIn('platform_revenue_mtd', response.data)
        self.assertIn('all_systems_operational', response.data)

    def test_sacco_admin_cannot_access_overview(self):
        """SACCO admin cannot access super admin overview."""
        self.client.force_authenticate(user=self.sacco_admin)
        response = self.client.get('/api/v1/management/superadmin/overview/')

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_all_systems_operational_false_when_critical_flag_exists(self):
        """all_systems_operational is False when CRITICAL flag exists."""
        ComplianceFlag.objects.create(
            sacco=self.sacco,
            flag_type=ComplianceFlag.FlagType.API_ISSUE,
            severity=ComplianceFlag.Severity.CRITICAL,
            status=ComplianceFlag.Status.OPEN,
            description='Critical API failure',
        )

        self.client.force_authenticate(user=self.super_admin)
        response = self.client.get('/api/v1/management/superadmin/overview/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data['all_systems_operational'])

    def test_all_systems_operational_true_without_critical_flags(self):
        """all_systems_operational is True when no CRITICAL flags exist."""
        ComplianceFlag.objects.create(
            sacco=self.sacco,
            flag_type=ComplianceFlag.FlagType.API_ISSUE,
            severity=ComplianceFlag.Severity.MEDIUM,
            status=ComplianceFlag.Status.OPEN,
            description='Medium priority issue',
        )

        self.client.force_authenticate(user=self.super_admin)
        response = self.client.get('/api/v1/management/superadmin/overview/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['all_systems_operational'])

    def test_platform_revenue_mtd_reflects_invoice_line_items(self):
        """platform_revenue_mtd must read real fee data (InvoiceLineItem),
        not the PlatformRevenue table nothing in production writes to."""
        _create_invoice_line_item(self.sacco, Decimal('15.00'))
        _create_invoice_line_item(self.sacco, Decimal('25.00'))
        # A PlatformRevenue row must be ignored -- if the view still read
        # from this table, it would report 999.00 instead of 40.00.
        PlatformRevenue.objects.create(
            sacco=self.sacco,
            revenue_type=PlatformRevenue.RevenueType.TRANSACTION_FEE,
            amount=Decimal('999.00'),
        )

        self.client.force_authenticate(user=self.super_admin)
        response = self.client.get('/api/v1/management/superadmin/overview/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            Decimal(str(response.data['platform_revenue_mtd'])),
            Decimal('40.00'),
        )


class PlatformRevenueChartTest(APITestCase):
    """Test PlatformRevenueChartView."""

    def setUp(self):
        """Set up test data."""
        self.client = APIClient()
        self.super_admin = User.objects.create_user(
            email='superadmin@saccosphere.com',
            password='testpass123',
        )
        Role.objects.create(
            user=self.super_admin,
            name=Role.SUPER_ADMIN,
            sacco=None,
        )

    def test_super_admin_can_access_revenue_chart(self):
        """Super admin can access revenue chart."""
        self.client.force_authenticate(user=self.super_admin)
        response = self.client.get('/api/v1/management/superadmin/revenue-chart/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsInstance(response.data, list)
        self.assertEqual(len(response.data), 12)

    def test_revenue_chart_has_required_fields(self):
        """Revenue chart data has required fields."""
        self.client.force_authenticate(user=self.super_admin)
        response = self.client.get('/api/v1/management/superadmin/revenue-chart/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        for month_data in response.data:
            self.assertIn('month', month_data)
            self.assertIn('saas_fees', month_data)
            self.assertIn('transaction_fees', month_data)
            self.assertIn('total_mrr', month_data)

    def test_current_month_transaction_fees_reflect_invoice_line_items(self):
        """The current month's bucket must sum real InvoiceLineItem fees;
        saas_fees stays 0 since subscription billing isn't active yet."""
        sacco = Sacco.objects.create(
            name='Chart SACCO', county='Nairobi', sector='EDUCATION',
        )
        _create_invoice_line_item(sacco, Decimal('30.00'))

        self.client.force_authenticate(user=self.super_admin)
        response = self.client.get('/api/v1/management/superadmin/revenue-chart/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        current_month = response.data[-1]
        self.assertEqual(
            Decimal(str(current_month['transaction_fees'])), Decimal('30.00'),
        )
        self.assertEqual(Decimal(str(current_month['saas_fees'])), Decimal('0.00'))
        self.assertEqual(
            Decimal(str(current_month['total_mrr'])), Decimal('30.00'),
        )


class TopSaccosTest(APITestCase):
    """Test TopSaccosView."""

    def setUp(self):
        """Set up test data."""
        self.client = APIClient()
        self.super_admin = User.objects.create_user(
            email='superadmin@saccosphere.com',
            password='testpass123',
        )
        Role.objects.create(
            user=self.super_admin,
            name=Role.SUPER_ADMIN,
            sacco=None,
        )

        self.sacco1 = Sacco.objects.create(
            name='High Volume SACCO',
            county='Nairobi',
            sector='EDUCATION',
        )
        self.sacco2 = Sacco.objects.create(
            name='Low Volume SACCO',
            county='Mombasa',
            sector='HEALTHCARE',
        )

    def test_super_admin_can_access_top_saccos(self):
        """Super admin can access top SACCOs."""
        self.client.force_authenticate(user=self.super_admin)
        response = self.client.get('/api/v1/management/superadmin/top-saccos/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsInstance(response.data, list)

    def test_top_saccos_has_required_fields(self):
        """Top SACCOs data has required fields."""
        self.client.force_authenticate(user=self.super_admin)
        response = self.client.get('/api/v1/management/superadmin/top-saccos/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        for sacco_data in response.data:
            self.assertIn('sacco_id', sacco_data)
            self.assertIn('sacco_name', sacco_data)
            self.assertIn('member_count', sacco_data)
            self.assertIn('txn_volume_this_month', sacco_data)
            self.assertIn('platform_fee_this_month', sacco_data)
            self.assertIn('health_status', sacco_data)

    def test_auto_created_flag_affects_health_status(self):
        """
        A ComplianceFlag created by a detector (not a manual admin entry)
        must surface here exactly like a manually-created one - the
        dashboard reads ComplianceFlag generically, it doesn't care who
        wrote the row.
        """
        from decimal import Decimal

        from payments.models import Transaction
        from saccomanagement.compliance_detectors import (
            PAYMENT_FAILURE_CONSECUTIVE_THRESHOLD,
            RepeatedPaymentFailureDetector,
        )

        payer = User.objects.create_user(
            email='top-saccos-payer@example.com', password='secret',
        )
        for index in range(PAYMENT_FAILURE_CONSECUTIVE_THRESHOLD):
            transaction = Transaction.objects.create(
                user=payer,
                sacco=self.sacco1,
                reference=f'TOP-SACCO-DET-{index}',
                transaction_type=Transaction.TransactionType.DEPOSIT,
                amount=Decimal('100.00'),
                status=Transaction.Status.FAILED,
            )
        RepeatedPaymentFailureDetector().check(transaction)

        self.client.force_authenticate(user=self.super_admin)
        response = self.client.get(
            '/api/v1/management/superadmin/top-saccos/',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        flagged = next(
            row for row in response.data
            if row['sacco_id'] == self.sacco1.id
        )
        self.assertEqual(flagged['health_status'], 'REVIEW')

    def test_platform_fee_this_month_reflects_invoice_line_items(self):
        """platform_fee_this_month must be scoped per-SACCO from
        InvoiceLineItem -- sacco2's fee must never bleed into sacco1's row."""
        _create_invoice_line_item(self.sacco1, Decimal('12.50'))
        _create_invoice_line_item(self.sacco2, Decimal('99.00'))

        self.client.force_authenticate(user=self.super_admin)
        response = self.client.get('/api/v1/management/superadmin/top-saccos/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # sacco_id serializes as a string (UUIDField.to_representation),
        # so compare against str(self.saccoN.id), not the UUID object.
        sacco1_row = next(
            row for row in response.data
            if row['sacco_id'] == str(self.sacco1.id)
        )
        sacco2_row = next(
            row for row in response.data
            if row['sacco_id'] == str(self.sacco2.id)
        )
        self.assertEqual(
            Decimal(str(sacco1_row['platform_fee_this_month'])),
            Decimal('12.50'),
        )
        self.assertEqual(
            Decimal(str(sacco2_row['platform_fee_this_month'])),
            Decimal('99.00'),
        )


class PlatformAlertsTest(APITestCase):
    """Test PlatformAlertsView."""

    def setUp(self):
        """Set up test data."""
        self.client = APIClient()
        self.super_admin = User.objects.create_user(
            email='superadmin@saccosphere.com',
            password='testpass123',
        )
        Role.objects.create(
            user=self.super_admin,
            name=Role.SUPER_ADMIN,
            sacco=None,
        )

        self.sacco = Sacco.objects.create(
            name='Alert SACCO',
            county='Nairobi',
            sector='EDUCATION',
        )

    def test_super_admin_can_access_alerts(self):
        """Super admin can access platform alerts."""
        ComplianceFlag.objects.create(
            sacco=self.sacco,
            flag_type=ComplianceFlag.FlagType.API_ISSUE,
            severity=ComplianceFlag.Severity.HIGH,
            status=ComplianceFlag.Status.OPEN,
            description='API issue',
        )

        self.client.force_authenticate(user=self.super_admin)
        response = self.client.get('/api/v1/management/superadmin/alerts/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsInstance(response.data, list)

    def test_alerts_only_shows_open_flags(self):
        """Alerts view only shows OPEN and INVESTIGATING flags."""
        ComplianceFlag.objects.create(
            sacco=self.sacco,
            flag_type=ComplianceFlag.FlagType.API_ISSUE,
            severity=ComplianceFlag.Severity.HIGH,
            status=ComplianceFlag.Status.RESOLVED,
            description='Resolved issue',
        )

        ComplianceFlag.objects.create(
            sacco=self.sacco,
            flag_type=ComplianceFlag.FlagType.PAYMENT_FAILURE,
            severity=ComplianceFlag.Severity.CRITICAL,
            status=ComplianceFlag.Status.OPEN,
            description='Open critical issue',
        )

        self.client.force_authenticate(user=self.super_admin)
        response = self.client.get('/api/v1/management/superadmin/alerts/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['severity'], 'CRITICAL')

    def test_auto_created_flag_appears_in_alerts(self):
        """A detector-created flag surfaces here just like a manual one."""
        from decimal import Decimal

        from payments.models import Transaction
        from saccomanagement.compliance_detectors import (
            PAYMENT_FAILURE_CONSECUTIVE_THRESHOLD,
            RepeatedPaymentFailureDetector,
        )

        payer = User.objects.create_user(
            email='alerts-payer@example.com', password='secret',
        )
        for index in range(PAYMENT_FAILURE_CONSECUTIVE_THRESHOLD):
            transaction = Transaction.objects.create(
                user=payer,
                sacco=self.sacco,
                reference=f'ALERTS-DET-{index}',
                transaction_type=Transaction.TransactionType.DEPOSIT,
                amount=Decimal('100.00'),
                status=Transaction.Status.FAILED,
            )
        RepeatedPaymentFailureDetector().check(transaction)

        self.client.force_authenticate(user=self.super_admin)
        response = self.client.get('/api/v1/management/superadmin/alerts/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['flag_type'], 'PAYMENT_FAILURE')
        self.assertEqual(response.data[0]['sacco_name'], self.sacco.name)


class LiveTransactionFeedTest(APITestCase):
    """Test LiveTransactionFeedView."""

    def setUp(self):
        """Set up test data."""
        self.client = APIClient()
        self.super_admin = User.objects.create_user(
            email='superadmin@saccosphere.com',
            password='testpass123',
        )
        Role.objects.create(
            user=self.super_admin,
            name=Role.SUPER_ADMIN,
            sacco=None,
        )

        self.sacco = Sacco.objects.create(
            name='Test SACCO',
            county='Nairobi',
            sector='EDUCATION',
        )
        self.user = User.objects.create_user(
            email='user@test.com',
            password='testpass123',
            first_name='Test',
            last_name='User',
        )
        self.membership = Membership.objects.create(
            user=self.user,
            sacco=self.sacco,
            member_number='001',
            status=Membership.Status.APPROVED,
        )

        self.provider = PaymentProvider.objects.create(
            name='M-Pesa',
            provider_type=PaymentProvider.ProviderType.MPESA,
        )

    def test_super_admin_can_access_live_transactions(self):
        """Super admin can access live transaction feed."""
        self.client.force_authenticate(user=self.super_admin)
        response = self.client.get('/api/v1/management/superadmin/transactions/live/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsInstance(response.data, list)

    def test_live_transactions_has_required_fields(self):
        """Live transaction data has required fields."""
        txn = Transaction.objects.create(
            user=self.user,
            provider=self.provider,
            reference='REF001',
            transaction_type=Transaction.TransactionType.DEPOSIT,
            amount=Decimal('1000.00'),
            status=Transaction.Status.COMPLETED,
        )

        MpesaTransaction.objects.create(
            transaction=txn,
            checkout_request_id='test_checkout',
            phone_number='254700000000',
            amount=Decimal('1000.00'),
        )

        self.client.force_authenticate(user=self.super_admin)
        response = self.client.get('/api/v1/management/superadmin/transactions/live/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        for txn_data in response.data:
            self.assertIn('sacco_name', txn_data)
            self.assertIn('user_name', txn_data)
            self.assertIn('amount', txn_data)
            self.assertIn('transaction_type', txn_data)
            self.assertIn('stk_status', txn_data)
            self.assertIn('created_at', txn_data)


class AllSaccosTest(APITestCase):
    """Test AllSaccosListView."""

    def setUp(self):
        """Set up test data."""
        self.client = APIClient()
        self.super_admin = User.objects.create_user(
            email='superadmin@saccosphere.com',
            password='testpass123',
        )
        Role.objects.create(
            user=self.super_admin,
            name=Role.SUPER_ADMIN,
            sacco=None,
        )

        self.sacco1 = Sacco.objects.create(
            name='SACCO 1',
            county='Nairobi',
            sector='EDUCATION',
        )
        self.sacco2 = Sacco.objects.create(
            name='SACCO 2',
            county='Mombasa',
            sector='HEALTHCARE',
        )

    def test_super_admin_can_access_all_saccos(self):
        """Super admin can access all SACCOs."""
        self.client.force_authenticate(user=self.super_admin)
        response = self.client.get('/api/v1/management/superadmin/saccos/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsInstance(response.data, list)
        self.assertEqual(len(response.data), 2)

    def test_all_saccos_has_required_fields(self):
        """All SACCOs data has required fields."""
        self.client.force_authenticate(user=self.super_admin)
        response = self.client.get('/api/v1/management/superadmin/saccos/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        for sacco_data in response.data:
            self.assertIn('id', sacco_data)
            self.assertIn('name', sacco_data)
            self.assertIn('member_count', sacco_data)
            self.assertIn('is_active', sacco_data)
            self.assertIn('created_at', sacco_data)
            self.assertIn('health_status', sacco_data)


class AllMembersTest(APITestCase):
    """Test AllMembersListView."""

    def setUp(self):
        """Set up test data."""
        self.client = APIClient()
        self.super_admin = User.objects.create_user(
            email='superadmin@saccosphere.com',
            password='testpass123',
        )
        Role.objects.create(
            user=self.super_admin,
            name=Role.SUPER_ADMIN,
            sacco=None,
        )

        self.sacco = Sacco.objects.create(
            name='Test SACCO',
            county='Nairobi',
            sector='EDUCATION',
        )

        self.user1 = User.objects.create_user(
            email='user1@test.com',
            password='testpass123',
            first_name='User',
            last_name='One',
        )
        self.user2 = User.objects.create_user(
            email='user2@test.com',
            password='testpass123',
            first_name='User',
            last_name='Two',
        )

        self.membership1 = Membership.objects.create(
            user=self.user1,
            sacco=self.sacco,
            member_number='001',
            status=Membership.Status.APPROVED,
        )
        self.membership2 = Membership.objects.create(
            user=self.user2,
            sacco=self.sacco,
            member_number='002',
            status=Membership.Status.APPROVED,
        )

    def test_super_admin_can_access_all_members(self):
        """Super admin can access all members."""
        self.client.force_authenticate(user=self.super_admin)
        response = self.client.get('/api/v1/management/superadmin/members/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('results', response.data)

    def test_all_members_pagination(self):
        """All members endpoint uses pagination."""
        self.client.force_authenticate(user=self.super_admin)
        response = self.client.get('/api/v1/management/superadmin/members/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('count', response.data)
        self.assertIn('results', response.data)

    def test_all_members_filter_by_sacco(self):
        """Can filter members by SACCO ID."""
        self.client.force_authenticate(user=self.super_admin)
        response = self.client.get(
            f'/api/v1/management/superadmin/members/?sacco_id={self.sacco.id}'
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 2)

    def test_all_members_search(self):
        """Can search members by email or name."""
        self.client.force_authenticate(user=self.super_admin)
        response = self.client.get(
            '/api/v1/management/superadmin/members/?search=user1'
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['email'], 'user1@test.com')
