import json
import os
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time, timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.core import mail
from django.core.management import call_command
from django.db import OperationalError, connection
from django.http import JsonResponse
from django.test import RequestFactory, TestCase, TransactionTestCase
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
    build_invoice_pdf,
    generate_monthly_sacco_invoice,
    previous_month_period,
    record_transaction_fee,
    send_invoice_to_sacco,
)
from billing.tasks import (
    generate_and_send_monthly_fee_reports,
    generate_monthly_invoices,
    send_invoice_email,
    suspend_overdue_saccos,
    update_overdue_invoices,
)
from health.models import JobHeartbeat
from payments.models import PaymentProvider, PlatformFee, Transaction
from saccomanagement.middleware import BillingSuspensionMiddleware
from saccomanagement.models import Role, SystemAuditLog
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

    def test_build_invoice_pdf_returns_none_when_weasyprint_unavailable(self):
        """A missing WeasyPrint must not fall back to CSV bytes mislabeled as a PDF."""
        record_transaction_fee(self.transaction, self.sacco)
        period_start, period_end = previous_month_period(timezone.localdate())
        PlatformFee.objects.filter(transaction=self.transaction).update(
            created_at=timezone.make_aware(
                datetime.combine(period_end, time.min)
            ),
        )
        invoice = generate_monthly_sacco_invoice(
            sacco=self.sacco,
            period_start=period_start,
            period_end=period_end,
        )

        with patch.dict('sys.modules', {'weasyprint': None}):
            pdf_content = build_invoice_pdf(invoice)

        self.assertIsNone(pdf_content)

    def test_send_invoice_to_sacco_omits_pdf_attachment_when_unavailable(self):
        """Email must carry only the real CSV, never a CSV mislabeled as PDF."""
        record_transaction_fee(self.transaction, self.sacco)
        period_start, period_end = previous_month_period(timezone.localdate())
        PlatformFee.objects.filter(transaction=self.transaction).update(
            created_at=timezone.make_aware(
                datetime.combine(period_end, time.min)
            ),
        )
        invoice = generate_monthly_sacco_invoice(
            sacco=self.sacco,
            period_start=period_start,
            period_end=period_end,
        )

        mail.outbox = []
        with patch.dict('sys.modules', {'weasyprint': None}):
            sent = send_invoice_to_sacco(invoice)

        self.assertTrue(sent)
        self.assertEqual(len(mail.outbox), 1)
        attachment_mimetypes = {
            attachment[2] for attachment in mail.outbox[0].attachments
        }
        self.assertEqual(attachment_mimetypes, {'text/csv'})

    @patch('billing.tasks.notify_superadmin.delay')
    def test_send_invoice_email_skips_and_alerts_when_pdf_missing(
        self,
        notify_mock,
    ):
        """A missing/unsaved PDF must not crash the Celery task."""
        invoice = Invoice.objects.create(
            sacco=self.sacco,
            invoice_number='SS-MISSING-PDF-001',
            billing_month=timezone.localdate().replace(day=1),
            total_amount=Decimal('50.00'),
            line_items_count=1,
            status='draft',
            due_date=timezone.localdate() + timedelta(days=7),
            pdf_path='',
        )

        send_invoice_email(str(invoice.id))

        notify_mock.assert_called_once()
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, 'draft')
        self.assertIsNone(invoice.sent_at)

    def test_send_invoice_email_retries_and_recovers_on_smtp_failure(self):
        """A transient SMTP failure must be retried and recovered from,
        not left as a permanently failed invoice."""
        with tempfile.NamedTemporaryFile(
            suffix='.pdf', delete=False,
        ) as tmp_pdf:
            tmp_pdf.write(b'%PDF-1.4 test')
            tmp_path = tmp_pdf.name

        invoice = Invoice.objects.create(
            sacco=self.sacco,
            invoice_number='SS-SMTP-FAIL-001',
            billing_month=timezone.localdate().replace(day=1),
            total_amount=Decimal('50.00'),
            line_items_count=1,
            status='draft',
            due_date=timezone.localdate() + timedelta(days=7),
            pdf_path=tmp_path,
        )

        try:
            with patch(
                'billing.tasks.EmailMessage.send',
                side_effect=[OSError('smtp unavailable'), 1],
            ):
                result = send_invoice_email.apply(args=[str(invoice.id)])
        finally:
            os.unlink(tmp_path)

        self.assertFalse(result.failed())
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, 'sent')

    @patch('config.utils.emit_metric')
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
        emit_metric_mock,
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
        emit_metric_mock.assert_called_once()
        metric_call_args, metric_call_kwargs = emit_metric_mock.call_args
        self.assertEqual(metric_call_args, ('billing_invoice_generated',))
        self.assertEqual(metric_call_kwargs['sacco_id'], str(self.sacco.id))
        self.assertEqual(
            metric_call_kwargs['invoice_number'], invoice.invoice_number,
        )
        self.assertEqual(
            Decimal(metric_call_kwargs['amount']), invoice.total_amount,
        )
        heartbeat = JobHeartbeat.objects.get(
            job_name='generate_monthly_invoices',
        )
        self.assertEqual(heartbeat.last_status, JobHeartbeat.Status.OK)
        self.assertEqual(heartbeat.detail['invoices_generated'], 1)
        self.assertEqual(heartbeat.detail['failures'], 0)

    @patch('config.utils.emit_metric')
    @patch('billing.tasks.notify_superadmin.delay')
    def test_update_overdue_invoices_records_heartbeat_and_metric(
        self,
        notify_delay_mock,
        emit_metric_mock,
    ):
        Invoice.objects.create(
            sacco=self.sacco,
            invoice_number='SS-OVERDUE-001',
            billing_month=timezone.localdate().replace(day=1),
            total_amount=Decimal('100.00'),
            line_items_count=1,
            status='sent',
            due_date=timezone.localdate() - timedelta(days=1),
        )

        update_overdue_invoices.apply()

        emit_metric_mock.assert_called_once_with(
            'billing_invoices_marked_overdue', count=1,
        )
        notify_delay_mock.assert_called_once()
        heartbeat = JobHeartbeat.objects.get(
            job_name='update_overdue_invoices',
        )
        self.assertEqual(heartbeat.last_status, JobHeartbeat.Status.OK)
        self.assertEqual(heartbeat.detail['invoices_marked_overdue'], 1)

    @patch('billing.tasks.notify_superadmin.delay')
    def test_update_overdue_invoices_retries_and_recovers_on_transient_db_error(
        self,
        notify_delay_mock,
    ):
        """A transient DB error must be retried and recovered from, not
        left as a silently-stuck sweep."""
        invoice = Invoice.objects.create(
            sacco=self.sacco,
            invoice_number='SS-DB-RETRY-001',
            billing_month=timezone.localdate().replace(day=1),
            total_amount=Decimal('50.00'),
            line_items_count=1,
            status='sent',
            due_date=timezone.localdate() - timedelta(days=1),
        )
        real_filter = Invoice.objects.filter
        attempts = {'count': 0}

        def flaky_filter(*args, **kwargs):
            attempts['count'] += 1
            if attempts['count'] == 1:
                raise OperationalError('db unavailable')
            return real_filter(*args, **kwargs)

        with patch(
            'billing.tasks.Invoice.objects.filter',
            side_effect=flaky_filter,
        ):
            result = update_overdue_invoices.apply()

        self.assertFalse(result.failed())
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, 'overdue')


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
        results = response.data['data']['results']
        invoice_ids = {item['id'] for item in results}
        self.assertIn(str(self.invoice_a.id), invoice_ids)
        self.assertNotIn(str(self.invoice_b_new.id), invoice_ids)

    def test_super_admin_invoice_list_can_filter_by_sacco(self):
        self.client.force_authenticate(user=self.super_admin)

        response = self.client.get(
            reverse('billing:invoice-list'),
            {'sacco_id': str(self.sacco_b.id)},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data['data']['results']
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['id'], str(self.invoice_b_new.id))

    def test_invoice_list_response_is_paginated(self):
        """InvoiceListView must no longer return an unbounded bare array."""
        self.client.force_authenticate(user=self.super_admin)

        response = self.client.get(reverse('billing:invoice-list'))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['success'])
        data = response.data['data']
        self.assertIn('count', data)
        self.assertIn('total_pages', data)
        self.assertIn('current_page', data)
        self.assertIn('results', data)

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

    @patch('config.utils.emit_metric')
    @patch('billing.tasks.send_suspension_notice.delay')
    def test_suspend_overdue_saccos_locks_sacco_admin_writes(
        self,
        notice_delay_mock,
        emit_metric_mock,
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
        emit_metric_mock.assert_called_once_with(
            'billing_sacco_suspended',
            sacco_id=str(self.sacco.id),
            invoice_number=self.invoice.invoice_number,
        )
        audit_entry = SystemAuditLog.objects.get(
            action='SACCO_BILLING_SUSPENDED',
            resource_type='Sacco',
            resource_id=str(self.sacco.id),
        )
        self.assertIsNone(audit_entry.user)
        self.assertEqual(
            audit_entry.new_values['invoice_number'],
            self.invoice.invoice_number,
        )
        heartbeat = JobHeartbeat.objects.get(
            job_name='suspend_overdue_saccos',
        )
        self.assertEqual(heartbeat.detail['saccos_suspended'], 1)

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

    def test_billing_suspension_exempts_invoice_write_subactions(self):
        """A suspended SACCO admin must still be able to act on their own
        invoices (e.g. resend) in order to resolve the suspension -- the
        exemption is a path prefix, not just the bare list endpoint."""
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

        self.assertEqual(response.status_code, 200)

    def test_billing_suspension_still_blocks_unrelated_write_paths(self):
        """The invoices/ prefix exemption must not widen into a blanket bypass."""
        self.sacco.is_billing_suspended = True
        self.sacco.save(update_fields=['is_billing_suspended'])
        middleware = BillingSuspensionMiddleware(
            lambda request: JsonResponse({'ok': True}),
        )
        request = self.factory.post('/api/v1/billing/other-endpoint/')
        request.user = self.admin

        response = middleware(request)

        self.assertEqual(response.status_code, 402)

    @patch('config.utils.emit_metric')
    @patch('billing.views.send_payment_received_notice.delay')
    def test_mark_paid_records_payment_and_restores_sacco(
        self,
        notice_delay_mock,
        emit_metric_mock,
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
        emit_metric_mock.assert_called_once_with(
            'billing_invoice_marked_paid',
            sacco_id=str(self.sacco.id),
            invoice_number=self.invoice.invoice_number,
            amount=str(Decimal('5000.00')),
        )
        audit_entry = SystemAuditLog.objects.get(
            action='INVOICE_MARKED_PAID',
            resource_type='Invoice',
            resource_id=str(self.invoice.id),
        )
        self.assertEqual(audit_entry.user, self.super_admin)
        self.assertEqual(audit_entry.old_values['invoice_status'], 'overdue')
        self.assertEqual(audit_entry.new_values['invoice_status'], 'paid')
        self.assertEqual(audit_entry.new_values['payment_ref'], 'MPESA_REF')

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

    @patch('billing.views.send_payment_received_notice.delay')
    def test_mark_paid_rejects_duplicate_call_on_already_paid_invoice(
        self,
        notice_delay_mock,
    ):
        """A retried/duplicate mark-paid call must not create a second payment."""
        self.client.force_authenticate(user=self.super_admin)
        payload = {
            'payment_ref': 'MPESA_REF',
            'amount': '5000.00',
            'payment_method': 'mpesa',
        }
        url = reverse(
            'billing:invoice-mark-paid',
            kwargs={'invoice_id': self.invoice.id},
        )

        first_response = self.client.post(url, payload, format='json')
        self.assertEqual(first_response.status_code, status.HTTP_200_OK)

        second_response = self.client.post(url, payload, format='json')

        self.assertEqual(second_response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(InvoicePayment.objects.filter(invoice=self.invoice).count(), 1)
        notice_delay_mock.assert_called_once()


class ReconcileUncollectedFeesCommandTests(TestCase):
    """Regression coverage for the reconcile_uncollected_fees command."""

    def setUp(self):
        self.sacco = Sacco.objects.create(
            name='Reconcile SACCO',
            registration_number='RECON001',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
            membership_type=Sacco.MembershipType.OPEN,
        )
        self.user = User.objects.create_user(
            email='reconcile.member@example.com',
            first_name='Reconcile',
            last_name='Member',
            phone_number='254700001188',
            password='StrongPass1',
        )
        provider, _ = PaymentProvider.objects.get_or_create(
            name='M-Pesa',
            defaults={
                'provider_type': PaymentProvider.ProviderType.MPESA,
                'is_active': True,
            },
        )
        pre_fix_transaction = Transaction.objects.create(
            provider=provider,
            user=self.user,
            reference='RECON-TXN-PRE-FIX',
            transaction_type=Transaction.TransactionType.DEPOSIT,
            amount=Decimal('1000.00'),
            status=Transaction.Status.COMPLETED,
            description='Pre-fix transaction with no gross_amount metadata',
            metadata={},
        )
        self.uncollected_revenue = PlatformRevenue.objects.create(
            sacco=self.sacco,
            transaction=pre_fix_transaction,
            revenue_type=PlatformRevenue.RevenueType.TRANSACTION_FEE,
            amount=Decimal('20.00'),
        )

    def test_command_identifies_uncollected_records_without_crashing(self):
        """Regression test: a NameError (sacacco_name typo) previously crashed
        the command on this exact path -- any run that found an uncollected
        record."""
        out = StringIO()

        # No --before given -> command defaults the cutoff to timezone.now(),
        # which is what exercises the breakdown-by-sacco branch that the
        # sacacco_name typo used to crash on for any matching record.
        call_command('reconcile_uncollected_fees', stdout=out)

        output = out.getvalue()
        self.assertIn('Uncollected fee records identified: 1', output)
        self.assertIn(self.sacco.name, output)

    def test_command_flags_records_only_when_executed(self):
        out = StringIO()

        call_command(
            'reconcile_uncollected_fees',
            '--mark-uncollected',
            '--execute',
            stdout=out,
        )

        self.uncollected_revenue.refresh_from_db()
        self.assertFalse(self.uncollected_revenue.is_collected)


class InvoiceLineItemImmutabilityTest(TestCase):
    """InvoiceLineItem is append-only at the model layer (mirrors
    ledger.tests.test_immutability.LedgerEntryImmutabilityTest)."""

    def setUp(self):
        self.sacco = Sacco.objects.create(
            name='Immutable Line Item SACCO',
            registration_number='IMM001',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
            membership_type=Sacco.MembershipType.OPEN,
        )
        self.user = User.objects.create_user(
            email='immutable-line-item@example.com',
            first_name='Immutable',
            last_name='Member',
            phone_number='254700001200',
            password='StrongPass1',
        )
        provider, _ = PaymentProvider.objects.get_or_create(
            name='M-Pesa',
            defaults={
                'provider_type': PaymentProvider.ProviderType.MPESA,
                'is_active': True,
            },
        )
        transaction = Transaction.objects.create(
            provider=provider,
            user=self.user,
            reference='IMM-TXN-001',
            transaction_type=Transaction.TransactionType.DEPOSIT,
            amount=Decimal('1000.00'),
            status=Transaction.Status.COMPLETED,
            sacco=self.sacco,
        )
        self.line_item = InvoiceLineItem.objects.create(
            sacco=self.sacco,
            transaction=transaction,
            transaction_type='deposit',
            gross_amount=Decimal('1010.00'),
            net_amount=Decimal('1000.00'),
            platform_fee=Decimal('10.00'),
            fee_model='percentage',
            rate_applied='1.0% of deposit amount',
            billing_month=timezone.localdate().replace(day=1),
            invoiced=False,
        )

    def test_posted_line_item_cannot_be_saved_again(self):
        self.line_item.platform_fee = Decimal('999.00')
        with self.assertRaises(PermissionError):
            self.line_item.save()

        self.line_item.refresh_from_db()
        self.assertEqual(self.line_item.platform_fee, Decimal('10.00'))

    def test_posted_line_item_cannot_be_deleted(self):
        with self.assertRaises(PermissionError):
            self.line_item.delete()

        self.assertTrue(
            InvoiceLineItem.objects.filter(pk=self.line_item.pk).exists()
        )

    def test_bulk_update_to_mark_invoiced_still_works(self):
        """InvoiceGenerator.generate() marks items invoiced via a bulk
        QuerySet.update(), which bypasses save() and must keep working."""
        InvoiceLineItem.objects.filter(pk=self.line_item.pk).update(
            invoiced=True,
        )

        self.line_item.refresh_from_db()
        self.assertTrue(self.line_item.invoiced)


class InvoiceMarkPaidConcurrencyTests(TransactionTestCase):
    """Two simultaneous mark-paid requests for one invoice must not both
    succeed. The threaded case needs real row-level locking, so it runs
    on PostgreSQL and skips on SQLite (select_for_update is a no-op
    there), matching services.tests.test_concurrency_limits.
    """

    def setUp(self):
        self.sacco = Sacco.objects.create(
            name='Mark-Paid Race SACCO',
            registration_number='MPR001',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
            membership_type=Sacco.MembershipType.OPEN,
        )
        self.super_admin = User.objects.create_user(
            email='mark-paid-race-admin@example.com',
            first_name='Race',
            last_name='Admin',
            phone_number='254700001199',
            password='StrongPass1',
            is_staff=True,
        )
        self.invoice = Invoice.objects.create(
            sacco=self.sacco,
            invoice_number='SS-2026-07-RACE',
            billing_month=timezone.datetime(2026, 7, 1).date(),
            total_amount=Decimal('5000.00'),
            line_items_count=1,
            status='overdue',
            due_date=timezone.localdate() - timedelta(days=9),
        )
        self.url = reverse(
            'billing:invoice-mark-paid',
            kwargs={'invoice_id': self.invoice.id},
        )

    def _mark_paid(self):
        client = APIClient()
        client.force_authenticate(user=self.super_admin)
        return client.post(
            self.url,
            {
                'payment_ref': 'MPESA_REF',
                'amount': '5000.00',
                'payment_method': 'mpesa',
            },
            format='json',
        )

    def test_concurrent_mark_paid_admits_exactly_one(self):
        if connection.vendor == 'sqlite':
            self.skipTest(
                'SQLite select_for_update is a no-op; run on PostgreSQL.'
            )

        barrier = threading.Barrier(2)
        results = []

        def worker(_):
            barrier.wait()
            try:
                results.append(self._mark_paid().status_code)
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(worker, range(2)))

        self.assertEqual(sorted(results), [200, 409])
        self.assertEqual(
            InvoicePayment.objects.filter(invoice=self.invoice).count(), 1,
        )
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, 'paid')
