from dataclasses import dataclass
from decimal import Decimal
from unittest.mock import patch

from django.core.signing import TimestampSigner
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from accounts.models import Sacco, User
from billing.models import InvoiceLineItem
from ledger.models import LedgerEntry
from payments.models import Transaction
from saccomembership.models import Membership
from services.disbursement_service import DisbursementService
from services.models import DisbursementAuditLog, Loan, LoanType
from services.tasks import (
    on_disbursement_b2c_callback,
    send_disbursement_confirmation_request,
)


@dataclass
class FakeDisbursementResult:
    conversation_id: str = 'CONV-123'
    success: bool = True
    error_message: str = ''
    raw_response: dict | None = None

    def __post_init__(self):
        if self.raw_response is None:
            self.raw_response = {'ConversationID': self.conversation_id}


class FakeProvider:
    def __init__(self):
        self.disbursement_calls = []

    def disburse(self, **kwargs):
        self.disbursement_calls.append(kwargs)
        return FakeDisbursementResult()

    def get_provider_name(self):
        return 'mock'


class DisbursementFraudPreventionTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.request_factory = RequestFactory()
        self.member = User.objects.create_user(
            email='member@example.com',
            first_name='Member',
            last_name='Borrower',
            phone_number='254712345678',
            password='StrongPass1',
        )
        self.admin = User.objects.create_user(
            email='admin@example.com',
            first_name='Sacco',
            last_name='Admin',
            password='StrongPass1',
        )
        self.sacco = Sacco.objects.create(
            name='Fraud Guard SACCO',
            registration_number='FG001',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
            membership_type=Sacco.MembershipType.OPEN,
        )
        self.membership = Membership.objects.create(
            user=self.member,
            sacco=self.sacco,
            status=Membership.Status.APPROVED,
            member_number='FG001-M001',
        )
        self.loan_type = LoanType.objects.create(
            sacco=self.sacco,
            name='Development Loan',
            interest_rate=Decimal('12.00'),
            max_term_months=24,
            min_amount=Decimal('1000.00'),
        )
        self.loan = Loan.objects.create(
            membership=self.membership,
            loan_type=self.loan_type,
            amount=Decimal('100000.00'),
            interest_rate=Decimal('12.00'),
            term_months=12,
            status=Loan.Status.APPROVED,
        )

    @patch('services.disbursement_service.get_psp_provider')
    def test_initiate_sends_net_and_stores_conversation_id(self, provider_mock):
        provider = FakeProvider()
        provider_mock.return_value = provider
        request = self.request_factory.post(
            '/',
            HTTP_X_FORWARDED_FOR='10.0.0.1',
        )

        result = DisbursementService().initiate(
            self.loan,
            self.admin,
            request,
        )

        self.loan.refresh_from_db()
        tx = self.loan.disbursement_transaction

        self.assertEqual(result['status'], Loan.DisbursementStatus.INITIATED)
        self.assertEqual(self.loan.mpesa_conversation_id, 'CONV-123')
        self.assertEqual(tx.gross_amount, Decimal('100000.00'))
        self.assertEqual(tx.platform_fee, Decimal('350.00'))
        self.assertEqual(tx.amount, Decimal('99650.00'))
        self.assertEqual(
            provider.disbursement_calls[0]['amount'],
            Decimal('99650.00'),
        )
        self.assertEqual(
            list(
                DisbursementAuditLog.objects.filter(loan=self.loan)
                .values_list('event', flat=True)
            ),
            ['LOAN_APPROVED', 'B2C_INITIATED'],
        )
        self.assertFalse(InvoiceLineItem.objects.filter(transaction=tx).exists())

    @patch('services.tasks.send_disbursement_confirmation_request.delay')
    def test_callback_records_gross_ledger_without_invoice(self, notify_mock):
        tx = self._attach_disbursement_transaction()

        on_disbursement_b2c_callback(
            str(self.loan.id),
            {
                'ResultCode': 0,
                'TransactionID': 'MPESA-TX-123',
            },
        )

        self.loan.refresh_from_db()
        ledger_entry = LedgerEntry.objects.get(transaction=tx)

        self.assertEqual(
            self.loan.disbursement_status,
            Loan.DisbursementStatus.DISBURSED,
        )
        self.assertEqual(self.loan.mpesa_transaction_id, 'MPESA-TX-123')
        self.assertEqual(ledger_entry.entry_type, LedgerEntry.EntryType.DEBIT)
        self.assertEqual(ledger_entry.amount, Decimal('100000.00'))
        self.assertIn('Received: KES 99,650.00', ledger_entry.description)
        self.assertTrue(
            DisbursementAuditLog.objects.filter(
                loan=self.loan,
                event='B2C_CALLBACK_RECEIVED',
            ).exists()
        )
        self.assertFalse(InvoiceLineItem.objects.filter(transaction=tx).exists())
        notify_mock.assert_called_once_with(str(self.loan.id))

    @patch('services.tasks.auto_resolve_disbursement.apply_async')
    @patch('services.tasks.notify_user_task.delay')
    @patch('notifications.tasks.send_sms_task.delay')
    @override_settings(FRONTEND_BASE_URL='https://app.example.test')
    def test_member_notification_quotes_net_amount(
        self,
        sms_mock,
        notify_mock,
        auto_resolve_mock,
    ):
        self._attach_disbursement_transaction()

        send_disbursement_confirmation_request(str(self.loan.id))

        sms_message = sms_mock.call_args.args[1]
        notify_kwargs = notify_mock.call_args.kwargs
        self.assertIn('KES 99,650', sms_message)
        self.assertNotIn('KES 100,000', sms_message)
        self.assertIn('confirm-disbursement', notify_kwargs['action_url'])
        self.assertIn('dispute-disbursement', notify_kwargs['secondary_url'])
        self.assertEqual(
            notify_kwargs['secondary_label'],
            'No, I did not receive it',
        )
        auto_resolve_mock.assert_called_once()

    def test_confirm_endpoint_creates_deferred_invoice_item_once(self):
        tx = self._attach_disbursement_transaction(
            disbursement_status=Loan.DisbursementStatus.DISBURSED,
        )
        token = TimestampSigner().sign(str(self.loan.id))
        url = reverse('services:confirm-disbursement')

        response = self.client.get(url, {'token': token})
        duplicate_response = self.client.get(url, {'token': token})

        self.loan.refresh_from_db()
        line_item = InvoiceLineItem.objects.get(transaction=tx)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(duplicate_response.status_code, 200)
        self.assertEqual(
            self.loan.disbursement_status,
            Loan.DisbursementStatus.MEMBER_CONFIRMED,
        )
        self.assertEqual(line_item.platform_fee, Decimal('350.00'))
        self.assertEqual(line_item.net_amount, Decimal('99650.00'))
        self.assertEqual(
            DisbursementAuditLog.objects.filter(
                loan=self.loan,
                event='MEMBER_CONFIRMED',
            ).count(),
            1,
        )

    def test_audit_log_is_append_only(self):
        log = DisbursementAuditLog.objects.create(
            loan=self.loan,
            event='LOAN_APPROVED',
            actor=self.admin,
            actor_role='sacco_admin',
            details={'loan_amount': str(self.loan.amount)},
        )

        log.details = {'changed': True}
        with self.assertRaises(PermissionError):
            log.save()

        with self.assertRaises(PermissionError):
            log.delete()

    def _attach_disbursement_transaction(self, disbursement_status=None):
        tx = Transaction.objects.create(
            sacco=self.sacco,
            user=self.member,
            reference=f'TEST-DSB-{self.loan.id}',
            transaction_type=Transaction.TransactionType.LOAN_DISBURSEMENT,
            amount=Decimal('99650.00'),
            gross_amount=Decimal('100000.00'),
            platform_fee=Decimal('350.00'),
            status=Transaction.Status.SENT,
        )
        self.loan.disbursement_transaction = tx
        self.loan.mpesa_conversation_id = 'CONV-123'
        if disbursement_status:
            self.loan.disbursement_status = disbursement_status
        self.loan.save(
            update_fields=[
                'disbursement_transaction',
                'mpesa_conversation_id',
                'disbursement_status',
                'updated_at',
            ],
        )
        return tx
