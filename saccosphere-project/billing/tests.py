from datetime import datetime, time
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models import Sacco, User
from billing.models import (
    Invoice,
    InvoiceLineItem,
    MonthlySaccoInvoice,
    PlatformRevenue,
)
from billing.services import (
    generate_monthly_sacco_invoice,
    previous_month_period,
    record_transaction_fee,
)
from billing.tasks import (
    generate_and_send_monthly_fee_reports,
    generate_monthly_invoices,
)
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

    def test_new_monthly_invoice_task_sums_invoice_line_item_fees(self):
        """New invoice task totals platform_fee from append-only line items."""
        billing_month = timezone.localdate().replace(day=1)
        self.transaction.gross_amount = Decimal('1010.00')
        self.transaction.platform_fee = Decimal('10.00')
        self.transaction.sacco = self.sacco
        self.transaction.save(
            update_fields=[
                'gross_amount',
                'platform_fee',
                'sacco',
                'updated_at',
            ]
        )
        InvoiceLineItem.objects.create(
            sacco=self.sacco,
            transaction=self.transaction,
            transaction_type=Transaction.TransactionType.DEPOSIT,
            gross_amount=Decimal('1010.00'),
            net_amount=Decimal('1000.00'),
            platform_fee=Decimal('10.00'),
            fee_model='percentage',
            rate_applied='1.0% of deposit amount',
            billing_month=billing_month,
            invoiced=False,
        )

        result = generate_monthly_invoices.apply()

        invoice = Invoice.objects.get(sacco=self.sacco)
        self.assertEqual(result.result, [str(invoice.id)])
        self.assertEqual(invoice.billing_month, billing_month)
        self.assertEqual(invoice.total_amount, Decimal('10.00'))
        self.assertEqual(invoice.line_items_count, 1)


class MonthlyInvoiceAccessTests(TestCase):
    """Validate invoice object permissions across SACCO tenants."""

    def setUp(self):
        self.client = APIClient()
        self.sacco_a = Sacco.objects.create(
            name='Invoice SACCO A',
            registration_number='INV001',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
            membership_type=Sacco.MembershipType.OPEN,
        )
        self.sacco_b = Sacco.objects.create(
            name='Invoice SACCO B',
            registration_number='INV002',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
            membership_type=Sacco.MembershipType.OPEN,
        )
        self.admin_a = User.objects.create_user(
            email='invoice.admin.a@example.com',
            first_name='Invoice',
            last_name='Admin',
            phone_number='254700001144',
            password='StrongPass1',
        )
        Role.objects.create(
            user=self.admin_a,
            sacco=self.sacco_a,
            name=Role.SACCO_ADMIN,
        )
        self.invoice_b = MonthlySaccoInvoice.objects.create(
            sacco=self.sacco_b,
            period_start=timezone.datetime(2024, 1, 1).date(),
            period_end=timezone.datetime(2024, 1, 31).date(),
            amount_due=Decimal('100.00'),
            report_payload={
                'period_start': '2024-01-01',
                'period_end': '2024-01-31',
                'transaction_count': 1,
                'total_transacted_amount': '5000.00',
                'fee_rate': '2%',
                'amount_due': '100.00',
                'payment_account_name': 'SaccoSphere Ltd',
                'payment_account_number': 'N/A',
                'payment_paybill': 'N/A',
            },
        )

    @patch('billing.views.send_invoice_to_sacco')
    def test_sacco_admin_cannot_resend_other_sacco_invoice(self, send_mock):
        """SACCO A admin cannot resend SACCO B invoices."""
        self.client.force_authenticate(user=self.admin_a)
        response = self.client.post(
            reverse(
                'billing:invoice-resend',
                kwargs={'invoice_id': self.invoice_b.id},
            ),
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        send_mock.assert_not_called()

    def test_sacco_admin_cannot_download_other_sacco_invoice(self):
        """SACCO A admin cannot download SACCO B invoices."""
        self.client.force_authenticate(user=self.admin_a)
        response = self.client.get(
            reverse(
                'billing:invoice-download',
                kwargs={'invoice_id': self.invoice_b.id},
            ),
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
