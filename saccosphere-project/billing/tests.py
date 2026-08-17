import json
from datetime import datetime, time, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.http import JsonResponse
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models import Sacco, User
from billing.models import (
    Invoice,
    InvoiceLineItem,
    InvoicePayment,
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
    suspend_overdue_saccos,
)
from payments.models import PaymentProvider, PlatformFee, Transaction
from saccomanagement.models import Role
from saccomanagement.middleware import BillingSuspensionMiddleware
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

    @patch('billing.tasks.update_overdue_invoices.delay')
    @patch('billing.tasks.send_invoice_email.delay')
    @patch('billing.pdf_generator.InvoicePDFGenerator.save')
    @patch('billing.pdf_generator.InvoicePDFGenerator.generate')
    def test_new_monthly_invoice_task_sums_invoice_line_item_fees(
        self,
        pdf_generate_mock,
        pdf_save_mock,
        email_delay_mock,
        overdue_delay_mock,
    ):
        """New invoice task totals platform_fee from append-only line items."""
        today = timezone.localdate()
        first_of_current_month = today.replace(day=1)
        billing_month = (
            first_of_current_month - timedelta(days=1)
        ).replace(day=1)
        pdf_generate_mock.return_value = b'%PDF-1.4 test'
        pdf_save_mock.return_value = '/tmp/test-invoice.pdf'

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
        self.assertIsNone(result.result)
        self.assertEqual(invoice.billing_month, billing_month)
        self.assertEqual(invoice.total_amount, Decimal('10.00'))
        self.assertEqual(invoice.line_items_count, 1)
        self.assertFalse(result.failed())
        email_delay_mock.assert_called_once_with(str(invoice.id))
        overdue_delay_mock.assert_called_once_with()


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
        self.super_admin = User.objects.create_user(
            email='invoice.superadmin@example.com',
            first_name='Invoice',
            last_name='Superadmin',
            phone_number='254700001145',
            password='StrongPass1',
        )
        self.member_a = User.objects.create_user(
            email='invoice.member.a@example.com',
            first_name='Invoice',
            last_name='Member',
            phone_number='254700001146',
            password='StrongPass1',
        )
        Role.objects.create(
            user=self.admin_a,
            sacco=self.sacco_a,
            name=Role.SACCO_ADMIN,
        )
        Role.objects.create(
            user=self.super_admin,
            sacco=None,
            name=Role.SUPER_ADMIN,
        )
        Membership.objects.create(
            user=self.member_a,
            sacco=self.sacco_a,
            status=Membership.Status.APPROVED,
            member_number='INV-M-001',
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
            user=self.member_a,
            reference='INV-TXN-001',
            transaction_type=Transaction.TransactionType.DEPOSIT,
            amount=Decimal('1000.00'),
            gross_amount=Decimal('1010.00'),
            platform_fee=Decimal('10.00'),
            status=Transaction.Status.COMPLETED,
            sacco=self.sacco_a,
        )
        self.preview_transaction = Transaction.objects.create(
            provider=provider,
            user=self.member_a,
            reference='INV-TXN-PREVIEW',
            transaction_type=Transaction.TransactionType.DEPOSIT,
            amount=Decimal('1000.00'),
            gross_amount=Decimal('1010.00'),
            platform_fee=Decimal('10.00'),
            status=Transaction.Status.COMPLETED,
            sacco=self.sacco_a,
        )
        current_month = timezone.localdate().replace(day=1)
        self.invoice_a = Invoice.objects.create(
            sacco=self.sacco_a,
            invoice_number='SS-2026-07-A',
            billing_month=current_month,
            total_amount=Decimal('10.00'),
            line_items_count=1,
            status='sent',
            due_date=timezone.localdate() + timedelta(days=5),
        )
        self.invoice_b_new = Invoice.objects.create(
            sacco=self.sacco_b,
            invoice_number='SS-2026-07-B',
            billing_month=current_month,
            total_amount=Decimal('20.00'),
            line_items_count=0,
            status='paid',
            due_date=timezone.localdate(),
            paid_at=timezone.now(),
        )
        InvoiceLineItem.objects.create(
            sacco=self.sacco_a,
            transaction=self.transaction,
            transaction_type=Transaction.TransactionType.DEPOSIT,
            gross_amount=Decimal('1010.00'),
            net_amount=Decimal('1000.00'),
            platform_fee=Decimal('10.00'),
            fee_model='percentage',
            rate_applied='1.0% of deposit amount',
            billing_month=current_month,
            invoiced=True,
            invoice=self.invoice_a,
        )
        InvoiceLineItem.objects.create(
            sacco=self.sacco_a,
            transaction=self.preview_transaction,
            transaction_type=Transaction.TransactionType.DEPOSIT,
            gross_amount=Decimal('1010.00'),
            net_amount=Decimal('1000.00'),
            platform_fee=Decimal('10.00'),
            fee_model='percentage',
            rate_applied='1.0% of deposit amount',
            billing_month=current_month,
            invoiced=False,
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

    def test_sacco_admin_cannot_download_other_sacco_invoice(self):
        """SACCO A admin cannot download SACCO B invoices."""
        self.client.force_authenticate(user=self.admin_a)
        response = self.client.get(
            reverse(
                'billing:invoice-download',
                kwargs={'invoice_id': self.invoice_b_new.id},
            ),
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_sacco_admin_invoice_list_is_sacco_scoped(self):
        self.client.force_authenticate(user=self.admin_a)

        response = self.client.get(reverse('billing:invoice-list'))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        invoice_ids = {item['id'] for item in response.data}
        self.assertIn(str(self.invoice_a.id), invoice_ids)
        self.assertNotIn(str(self.invoice_b_new.id), invoice_ids)

    def test_super_admin_invoice_list_can_filter_by_sacco(self):
        self.client.force_authenticate(user=self.super_admin)

        response = self.client.get(
            reverse('billing:invoice-list'),
            {'sacco_id': str(self.sacco_b.id)},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['id'], str(self.invoice_b_new.id))

    def test_invoice_detail_returns_line_items_and_by_type_summary(self):
        self.client.force_authenticate(user=self.admin_a)

        response = self.client.get(
            reverse('billing:invoice-detail', kwargs={'id': self.invoice_a.id}),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['line_items']), 1)
        self.assertEqual(
            response.data['line_items'][0]['transaction_ref'],
            'INV-TXN-001',
        )
        self.assertEqual(response.data['by_type']['deposit']['count'], 1)
        self.assertEqual(
            Decimal(response.data['by_type']['deposit']['total_fee']),
            Decimal('10.00'),
        )

    def test_revenue_summary_is_super_admin_only(self):
        self.client.force_authenticate(user=self.admin_a)

        response = self.client.get(reverse('billing:revenue-summary'))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_current_month_preview_returns_uninvoiced_running_total(self):
        self.client.force_authenticate(user=self.admin_a)

        response = self.client.get(
            reverse('billing:current-month-transactions'),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['transactions_count'], 1)
        self.assertEqual(
            Decimal(response.data['projected_invoice_total']),
            Decimal('10.00'),
        )
        self.assertEqual(response.data['by_type']['deposit']['count'], 1)


class BillingSuspensionTests(TestCase):
    """Validate SACCO billing suspension, blocking, and restoration."""

    def setUp(self):
        self.factory = RequestFactory()
        self.client = APIClient()
        self.sacco = Sacco.objects.create(
            name='Suspended Billing SACCO',
            registration_number='SUSP001',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
            membership_type=Sacco.MembershipType.OPEN,
        )
        self.admin = User.objects.create_user(
            email='suspended.admin@example.com',
            first_name='Suspended',
            last_name='Admin',
            phone_number='254700001155',
            password='StrongPass1',
        )
        self.member = User.objects.create_user(
            email='suspended.member@example.com',
            first_name='Suspended',
            last_name='Member',
            phone_number='254700001166',
            password='StrongPass1',
        )
        self.super_admin = User.objects.create_user(
            email='billing.superadmin@example.com',
            first_name='Billing',
            last_name='Super',
            phone_number='254700001177',
            password='StrongPass1',
            is_staff=True,
        )
        Role.objects.create(
            user=self.admin,
            sacco=self.sacco,
            name=Role.SACCO_ADMIN,
        )
        Role.objects.create(
            user=self.super_admin,
            sacco=None,
            name=Role.SUPER_ADMIN,
        )
        Membership.objects.create(
            user=self.member,
            sacco=self.sacco,
            status=Membership.Status.APPROVED,
            member_number='SUSP-M-001',
        )
        self.invoice = Invoice.objects.create(
            sacco=self.sacco,
            invoice_number='SS-2026-07-SUSP',
            billing_month=timezone.datetime(2026, 7, 1).date(),
            total_amount=Decimal('5000.00'),
            line_items_count=1,
            status='overdue',
            due_date=timezone.localdate() - timedelta(days=9),
        )

    @patch('billing.tasks.send_suspension_notice.delay')
    def test_suspend_overdue_saccos_locks_sacco_admin_writes(
        self,
        notice_delay_mock,
    ):
        count = suspend_overdue_saccos()

        self.assertEqual(count, 1)
        self.sacco.refresh_from_db()
        self.invoice.refresh_from_db()
        self.assertTrue(self.sacco.is_billing_suspended)
        self.assertIsNotNone(self.sacco.suspended_at)
        self.assertEqual(
            self.sacco.suspension_reason,
            'Invoice SS-2026-07-SUSP overdue by 9 days',
        )
        self.assertIsInstance(self.sacco.suspension_reason, str)
        self.assertEqual(self.invoice.status, 'suspended')
        notice_delay_mock.assert_called_once_with(
            str(self.sacco.id),
            str(self.invoice.id),
        )

    def test_billing_suspension_blocks_admin_write_with_402(self):
        self.sacco.is_billing_suspended = True
        self.sacco.save(update_fields=['is_billing_suspended'])
        middleware = BillingSuspensionMiddleware(
            lambda request: JsonResponse({'ok': True}),
        )
        request = self.factory.post('/api/v1/services/savings/')
        request.user = self.admin

        response = middleware(request)
        payload = json.loads(response.content)

        self.assertEqual(response.status_code, 402)
        self.assertEqual(payload['error'], 'BILLING_SUSPENDED')
        self.assertIsInstance(payload['message'], str)
        self.assertNotIn('(', payload['message'])

    def test_billing_suspension_allows_member_write_and_admin_read(self):
        self.sacco.is_billing_suspended = True
        self.sacco.save(update_fields=['is_billing_suspended'])
        middleware = BillingSuspensionMiddleware(
            lambda request: JsonResponse({'ok': True}),
        )

        member_request = self.factory.post('/api/v1/services/savings/')
        member_request.user = self.member
        member_response = middleware(member_request)

        admin_read_request = self.factory.get('/api/v1/services/savings/')
        admin_read_request.user = self.admin
        admin_read_response = middleware(admin_read_request)

        self.assertEqual(member_response.status_code, 200)
        self.assertEqual(admin_read_response.status_code, 200)

    def test_billing_suspension_blocks_admin_invoice_write_subactions(self):
        self.sacco.is_billing_suspended = True
        self.sacco.save(update_fields=['is_billing_suspended'])
        middleware = BillingSuspensionMiddleware(
            lambda request: JsonResponse({'ok': True}),
        )
        request = self.factory.post(
            f'/api/v1/billing/invoices/{self.invoice.id}/resend/',
        )
        request.user = self.admin

        response = middleware(request)

        self.assertEqual(response.status_code, 402)

    @patch('billing.views.send_payment_received_notice.delay')
    def test_mark_paid_records_payment_and_restores_sacco(
        self,
        notice_delay_mock,
    ):
        self.sacco.is_billing_suspended = True
        self.sacco.suspended_at = timezone.now()
        self.sacco.suspension_reason = 'Invoice overdue'
        self.sacco.save(
            update_fields=[
                'is_billing_suspended',
                'suspended_at',
                'suspension_reason',
            ],
        )
        self.client.force_authenticate(user=self.super_admin)

        response = self.client.post(
            reverse(
                'billing:invoice-mark-paid',
                kwargs={'invoice_id': self.invoice.id},
            ),
            {
                'payment_ref': 'MPESA_REF',
                'amount': '5000.00',
                'payment_method': 'mpesa',
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.invoice.refresh_from_db()
        self.sacco.refresh_from_db()
        self.assertEqual(self.invoice.status, 'paid')
        self.assertIsNotNone(self.invoice.paid_at)
        self.assertEqual(self.invoice.payment_reference, 'MPESA_REF')
        self.assertFalse(self.sacco.is_billing_suspended)
        self.assertIsNone(self.sacco.suspended_at)
        self.assertEqual(self.sacco.suspension_reason, '')
        self.assertTrue(
            InvoicePayment.objects.filter(
                invoice=self.invoice,
                amount=Decimal('5000.00'),
                payment_method='mpesa',
                payment_ref='MPESA_REF',
                recorded_by=self.super_admin,
            ).exists(),
        )
        notice_delay_mock.assert_called_once_with(
            str(self.sacco.id),
            str(self.invoice.id),
        )

    @patch('billing.views.send_payment_received_notice.delay')
    def test_mark_paid_rejects_underpayment(self, notice_delay_mock):
        self.client.force_authenticate(user=self.super_admin)

        response = self.client.post(
            reverse(
                'billing:invoice-mark-paid',
                kwargs={'invoice_id': self.invoice.id},
            ),
            {
                'payment_ref': 'MPESA_REF',
                'amount': '4999.99',
                'payment_method': 'mpesa',
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, 'overdue')
        self.assertFalse(InvoicePayment.objects.exists())
        notice_delay_mock.assert_not_called()
