from datetime import datetime, time
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from accounts.models import Sacco, User
from billing.models import MonthlySaccoInvoice, PlatformRevenue
from billing.services import (
    generate_monthly_sacco_invoice,
    previous_month_period,
    record_transaction_fee,
)
from billing.tasks import generate_and_send_monthly_fee_reports
from payments.models import PaymentProvider, PlatformFee, Transaction
from saccomanagement.models import Role
from saccomembership.models import Membership


class BillingAutomationTests(TestCase):
    """Validate 2% fee capture and monthly invoice automation behavior."""

    def setUp(self):
        """Create baseline users, SACCO, membership, and transaction context."""
        self.sacco = Sacco.objects.create(
            name='Billing SACCO',
            registration_number='BILL001',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
            membership_type=Sacco.MembershipType.OPEN,
        )
        self.user = User.objects.create_user(
            email='billing.member@example.com',
            first_name='Billing',
            last_name='Member',
            phone_number='254700001122',
            password='StrongPass1',
        )
        self.admin = User.objects.create_user(
            email='billing.admin@example.com',
            first_name='Billing',
            last_name='Admin',
            phone_number='254700001133',
            password='StrongPass1',
        )
        Role.objects.create(
            user=self.admin,
            sacco=self.sacco,
            name=Role.SACCO_ADMIN,
        )
        Membership.objects.create(
            user=self.user,
            sacco=self.sacco,
            status=Membership.Status.APPROVED,
            member_number='BILL-M-001',
        )
        provider, _ = PaymentProvider.objects.get_or_create(
            name='M-Pesa',
            defaults={
                'provider_type': PaymentProvider.ProviderType.MPESA,
                'is_active': True,
            },
        )
        self.transaction = Transaction.objects.create(
            provider=provider,
            user=self.user,
            reference='BILL-TXN-001',
            transaction_type=Transaction.TransactionType.DEPOSIT,
            amount=Decimal('1000.00'),
            status=Transaction.Status.COMPLETED,
            description='Billing test payment',
        )

    def test_record_transaction_fee_applies_two_percent(self):
        """Completed transactions should create a 2% platform fee record once."""
        platform_fee = record_transaction_fee(self.transaction, self.sacco)

        self.assertIsNotNone(platform_fee)
        self.assertEqual(platform_fee.amount, Decimal('20.00'))
        self.transaction.refresh_from_db()
        self.assertEqual(self.transaction.fee_amount, Decimal('20.00'))
        self.assertTrue(
            PlatformRevenue.objects.filter(
                transaction=self.transaction,
                revenue_type=PlatformRevenue.RevenueType.TRANSACTION_FEE,
                amount=Decimal('20.00'),
            ).exists()
        )

        # Idempotency check
        second_call = record_transaction_fee(self.transaction, self.sacco)
        self.assertEqual(platform_fee.id, second_call.id)
        self.assertEqual(
            PlatformFee.objects.filter(transaction=self.transaction).count(),
            1,
        )

    @patch('billing.services.EmailMessage.send')
    def test_monthly_invoice_generation_and_send(self, email_send_mock):
        """Monthly task should generate and send SACCO invoice report."""
        record_transaction_fee(self.transaction, self.sacco)
        period_start, period_end = previous_month_period(timezone.localdate())

        # Move fee to previous month so scheduled job picks it up
        PlatformFee.objects.filter(transaction=self.transaction).update(
            created_at=timezone.make_aware(
                datetime.combine(period_end, time.min)
            ),
        )

        count = generate_and_send_monthly_fee_reports()

        self.assertGreaterEqual(count, 1)
        invoice = MonthlySaccoInvoice.objects.get(
            sacco=self.sacco,
            period_start=period_start,
            period_end=period_end,
        )
        self.assertEqual(invoice.amount_due, Decimal('20.00'))
        self.assertEqual(
            invoice.report_payload.get('total_transacted_amount'),
            '1000',
        )
        self.assertEqual(invoice.status, MonthlySaccoInvoice.Status.SENT)
        email_send_mock.assert_called()
