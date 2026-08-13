from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from kombu.exceptions import OperationalError as KombuOperationalError
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models import Sacco, User
from billing.models import InvoiceLineItem
from ledger.models import LedgerEntry
from notifications.models import Notification
from payments.disbursements import initiate_b2c_loan_disbursement
from payments.models import (
    Callback,
    MpesaTransaction,
    PaymentProvider,
    Transaction,
)
from payments.tasks import (
    _apply_loan_repayment,
    _apply_saving_deposit,
    _create_callback_ledger_entry,
    _process_successful_callback,
    _record_platform_fee_for_sacco,
    process_b2c_callback_task,
    process_stk_callback_task,
)
from saccomanagement.models import Role
from saccomembership.models import Membership
from services.models import Loan, LoanType, RepaymentSchedule, Saving
from services.models import SavingsType

from payments.integrations.mpesa.daraja import DarajaClient, DarajaError


class FakeResponse:
    def raise_for_status(self):
        return None

    def json(self):
        raise ValueError('Invalid JSON')


class DarajaClientTests(TestCase):
    @override_settings(
        MPESA_CONSUMER_KEY='',
        MPESA_CONSUMER_SECRET='',
        MPESA_SHORTCODE='',
        MPESA_PASSKEY='',
        MPESA_CALLBACK_BASE_URL='',
    )
    def test_stk_push_requires_mpesa_settings(self):
        with self.assertRaisesMessage(
            DarajaError,
            (
                'M-Pesa configuration is missing: MPESA_CONSUMER_KEY, '
                'MPESA_CONSUMER_SECRET, MPESA_SHORTCODE, MPESA_PASSKEY, '
                'MPESA_CALLBACK_BASE_URL'
            ),
        ):
            DarajaClient().initiate_stk_push(
                phone_number='254712345678',
                amount='10.00',
                account_reference='SS-TEST',
                description='Test payment',
                callback_path='/api/v1/payments/callback/mpesa/stk/',
            )

    @patch('payments.integrations.mpesa.daraja.cache')
    @patch('payments.integrations.mpesa.daraja.requests.get')
    @override_settings(
        MPESA_CONSUMER_KEY='test-key',
        MPESA_CONSUMER_SECRET='test-secret',
    )
    def test_get_access_token_rejects_non_json_response(
        self,
        mock_get,
        mock_cache,
    ):
        mock_cache.get.return_value = None
        mock_get.return_value = FakeResponse()

        with self.assertRaisesMessage(
            DarajaError,
            'M-Pesa access token response was not valid JSON.',
        ):
            DarajaClient().get_access_token()

    @patch('payments.integrations.mpesa.daraja.requests.post')
    def test_post_rejects_non_json_response(self, mock_post):
        mock_post.return_value = FakeResponse()

        with self.assertRaisesMessage(
            DarajaError,
            'M-Pesa response was not valid JSON.',
        ):
            DarajaClient()._post(
                'https://example.test/mpesa',
                'test-token',
                {'Amount': 10},
            )


class DepositInitiateViewTests(TestCase):
    """Validate member SACCO scoping for deposit initiation."""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            email='member@example.com',
            first_name='Deposit',
            last_name='Member',
            phone_number='254712345678',
            password='StrongPass1',
        )
        self.member_sacco = Sacco.objects.create(
            name='Member SACCO',
            registration_number='DEP001',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
            membership_type=Sacco.MembershipType.OPEN,
        )
        self.other_sacco = Sacco.objects.create(
            name='Other SACCO',
            registration_number='DEP002',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
            membership_type=Sacco.MembershipType.OPEN,
        )
        Membership.objects.create(
            user=self.user,
            sacco=self.member_sacco,
            status=Membership.Status.APPROVED,
            member_number='DEP-M-001',
        )

    def test_user_cannot_deposit_into_unowned_sacco(self):
        """A user needs approved membership in the target SACCO."""
        self.client.force_authenticate(user=self.user)
        response = self.client.post(
            reverse('payments:deposit-initiate'),
            {
                'phone_number': '254712345678',
                'amount': '1000.00',
                'sacco_id': str(self.other_sacco.id),
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertIn('approved membership', response.data['detail'])
        self.assertFalse(Transaction.objects.exists())

    @override_settings(DEBUG=True, PAYMENT_PROVIDER='')
    def test_deposit_charges_gross_and_records_net_fee_breakdown(self):
        """Deposit initiation charges gross while recording net and fee."""
        self.client.force_authenticate(user=self.user)
        response = self.client.post(
            reverse('payments:deposit-initiate'),
            {
                'phone_number': '254712345678',
                'amount': '1000.00',
                'sacco_id': str(self.member_sacco.id),
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['amount_depositing'], 'KES 1,000.00')
        self.assertEqual(response.data['platform_fee'], 'KES 10.00')
        self.assertEqual(response.data['total_charged'], 'KES 1,010.00')
        self.assertEqual(response.data['savings_credited'], 'KES 1,000.00')
        self.assertEqual(response.data['status'], Transaction.Status.PENDING)

        transaction = Transaction.objects.get()
        self.assertEqual(transaction.amount, Decimal('1000.00'))
        self.assertEqual(transaction.fee_amount, Decimal('10.00'))
        self.assertEqual(transaction.metadata['net_amount'], '1000.00')
        self.assertEqual(transaction.metadata['platform_fee'], '10.00')
        self.assertEqual(transaction.metadata['gross_amount'], '1010.00')


class WithdrawalInitiateViewTests(TestCase):
    """Validate withdrawal initiation deducts the platform fee from B2C."""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            email='withdrawal.member@example.com',
            first_name='Withdrawal',
            last_name='Member',
            phone_number='254712345680',
            password='StrongPass1',
        )
        self.sacco = Sacco.objects.create(
            name='Withdrawal SACCO',
            registration_number='WD001',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
            membership_type=Sacco.MembershipType.OPEN,
        )
        self.membership = Membership.objects.create(
            user=self.user,
            sacco=self.sacco,
            status=Membership.Status.APPROVED,
            member_number='WD-M-001',
        )
        self.savings_type = SavingsType.objects.create(
            sacco=self.sacco,
            name=SavingsType.Name.BOSA,
            minimum_contribution=Decimal('100.00'),
        )
        self.saving = Saving.objects.create(
            membership=self.membership,
            savings_type=self.savings_type,
            amount=Decimal('6000.00'),
            total_contributions=Decimal('6000.00'),
            status=Saving.Status.ACTIVE,
        )

    @override_settings(DEBUG=True, PAYMENT_PROVIDER='')
    def test_withdrawal_sends_net_and_records_gross_fee_breakdown(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.post(
            reverse('payments:withdrawal-initiate'),
            {
                'phone_number': '254712345680',
                'amount': '5000.00',
                'sacco_id': str(self.sacco.id),
                'saving_id': str(self.saving.id),
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['amount_requested'], 'KES 5,000.00')
        self.assertEqual(response.data['platform_fee'], 'KES 25.00')
        self.assertEqual(response.data['amount_to_member'], 'KES 4,975.00')

        transaction = Transaction.objects.get(
            transaction_type=Transaction.TransactionType.WITHDRAWAL,
        )
        self.assertEqual(transaction.amount, Decimal('4975.00'))
        self.assertEqual(transaction.gross_amount, Decimal('5000.00'))
        self.assertEqual(transaction.platform_fee, Decimal('25.00'))
        self.assertEqual(transaction.fee_rate, None)
        self.assertEqual(transaction.sacco, self.sacco)
        self.assertEqual(transaction.status, Transaction.Status.SENT)
        self.assertEqual(transaction.metadata['saving_id'], str(self.saving.id))


class FeePreviewViewTests(TestCase):
    """Validate fee preview summaries before payment initiation."""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            email='fee.preview@example.com',
            phone_number='254712345679',
            password='StrongPass1',
        )

    def test_fee_preview_requires_authentication(self):
        response = self.client.get(
            reverse('payments:fee-preview'),
            {'type': 'deposit', 'amount': '1000'},
        )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_deposit_fee_preview_returns_gross_payment_summary(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.get(
            reverse('payments:fee-preview'),
            {'type': 'deposit', 'amount': '1000'},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['platform_fee'], Decimal('10.00'))
        self.assertEqual(response.data['gross_amount'], Decimal('1010.00'))
        self.assertEqual(response.data['net_amount'], Decimal('1000'))
        self.assertEqual(
            response.data['summary']['you_pay'],
            'KES 1,010.00',
        )
        self.assertEqual(
            response.data['summary']['credited_to_you'],
            'KES 1,000.00',
        )


class CallbackCreateViewTests(TestCase):
    """Validate generic PSP callback verification and async dispatch."""

    def setUp(self):
        self.client = APIClient()
        self.provider = PaymentProvider.objects.create(
            name='secure-psp',
            provider_type=PaymentProvider.ProviderType.INTERNAL,
            is_active=True,
        )

    @patch('payments.views.get_provider_class')
    def test_rejects_non_mpesa_callback_when_verification_fails(
        self,
        get_provider_class_mock,
    ):
        """Failed provider verification rejects the callback."""

        class RejectingProvider:
            def verify_webhook(self, request):
                return False

        get_provider_class_mock.return_value = RejectingProvider

        response = self.client.post(
            reverse('payments:callback-create'),
            {
                'provider': str(self.provider.id),
                'raw_payload': {'transaction_id': 'txn-001'},
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(Callback.objects.exists())

    @patch('payments.views.process_payment_callback.delay')
    @patch('payments.views.get_provider_class')
    def test_verified_non_mpesa_callback_is_enqueued(
        self,
        get_provider_class_mock,
        delay_mock,
    ):
        """Verified callbacks are saved and handed to Celery."""

        class AcceptingProvider:
            def verify_webhook(self, request):
                return True

        get_provider_class_mock.return_value = AcceptingProvider

        response = self.client.post(
            reverse('payments:callback-create'),
            {
                'provider': str(self.provider.id),
                'raw_payload': {'transaction_id': 'txn-002'},
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        callback = Callback.objects.get()
        delay_mock.assert_called_once_with(str(callback.id))


class STKStatusViewTests(TestCase):
    """Validate STK status lookup does not reveal transaction ownership."""

    def setUp(self):
        self.client = APIClient()
        self.owner = User.objects.create_user(
            email='stk.owner@example.com',
            phone_number='254712000001',
            password='StrongPass1',
        )
        self.other_user = User.objects.create_user(
            email='stk.other@example.com',
            phone_number='254712000002',
            password='StrongPass1',
        )
        provider = PaymentProvider.objects.create(
            name='M-Pesa',
            provider_type=PaymentProvider.ProviderType.MPESA,
            is_active=True,
        )
        transaction = Transaction.objects.create(
            provider=provider,
            user=self.owner,
            reference='STK-TXN-001',
            transaction_type=Transaction.TransactionType.DEPOSIT,
            amount='100.00',
            status=Transaction.Status.PENDING,
            description='STK status test',
        )
        self.mpesa_transaction = MpesaTransaction.objects.create(
            transaction=transaction,
            phone_number='254712000001',
            merchant_request_id='MERCHANT-001',
            checkout_request_id='ws_CO_001',
        )

    def test_mismatched_and_missing_checkout_ids_return_uniform_404(self):
        """Wrong-owner checkout IDs and missing IDs are indistinguishable."""
        self.client.force_authenticate(user=self.other_user)
        owned_by_someone_else = self.client.get(
            reverse(
                'payments:mpesa-stk-status',
                kwargs={
                    'checkout_request_id': (
                        self.mpesa_transaction.checkout_request_id
                    ),
                },
            ),
        )
        missing = self.client.get(
            reverse(
                'payments:mpesa-stk-status',
                kwargs={'checkout_request_id': 'ws_CO_missing'},
            ),
        )

        self.assertEqual(
            owned_by_someone_else.status_code,
            status.HTTP_404_NOT_FOUND,
        )
        self.assertEqual(missing.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(owned_by_someone_else.data, missing.data)


class MpesaCallbackAcknowledgementTests(TestCase):
    """Validate durable callback storage before retryable acknowledgements."""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            email='callback-ack-member@example.com',
            phone_number='254712300001',
            password='StrongPass1',
        )
        self.provider = PaymentProvider.objects.create(
            name='M-Pesa',
            provider_type=PaymentProvider.ProviderType.MPESA,
            is_active=True,
        )

    def _transaction(self, reference, transaction_type):
        return Transaction.objects.create(
            provider=self.provider,
            user=self.user,
            reference=reference,
            transaction_type=transaction_type,
            amount=Decimal('100.00'),
            status=Transaction.Status.PENDING,
            description='Callback acknowledgement test',
        )

    @patch('payments.views.is_safaricom_ip', return_value=True)
    @patch('payments.views.is_replay_attack', return_value=False)
    @patch('payments.tasks.process_stk_callback_task.delay')
    def test_stk_enqueue_failure_is_saved_and_returns_retry(
        self,
        delay_mock,
        _replay_mock,
        _ip_mock,
    ):
        delay_mock.side_effect = KombuOperationalError('broker unavailable')
        transaction = self._transaction(
            'CALLBACK-ACK-STK-001',
            Transaction.TransactionType.DEPOSIT,
        )
        mpesa_transaction = MpesaTransaction.objects.create(
            transaction=transaction,
            phone_number='254712300001',
            checkout_request_id='CALLBACK-ACK-CHECKOUT-001',
        )
        callback_body = {
            'Body': {
                'stkCallback': {
                    'CheckoutRequestID': (
                        mpesa_transaction.checkout_request_id
                    ),
                    'ResultCode': 0,
                    'ResultDesc': 'Success',
                },
            },
        }

        response = self.client.post(
            reverse('payments:mpesa-stk-callback'),
            callback_body,
            format='json',
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_503_SERVICE_UNAVAILABLE,
        )
        callback = Callback.objects.get()
        self.assertFalse(callback.processed)
        self.assertEqual(callback.transaction, transaction)
        self.assertEqual(callback.raw_payload['callback_type'], 'STK')
        self.assertEqual(callback.raw_payload['payload'], callback_body)
        self.assertIn('broker unavailable', callback.processing_error)

    @patch('payments.views.is_safaricom_ip', return_value=True)
    @patch('payments.views.is_replay_attack', return_value=False)
    @patch('payments.tasks.process_b2c_callback_task.delay')
    def test_b2c_enqueue_failure_is_saved_and_returns_retry(
        self,
        delay_mock,
        _replay_mock,
        _ip_mock,
    ):
        delay_mock.side_effect = KombuOperationalError('broker unavailable')
        transaction = self._transaction(
            'CALLBACK-ACK-B2C-001',
            Transaction.TransactionType.LOAN_DISBURSEMENT,
        )
        mpesa_transaction = MpesaTransaction.objects.create(
            transaction=transaction,
            phone_number='254712300001',
            conversation_id='CALLBACK-ACK-CONVERSATION-001',
            transaction_type=MpesaTransaction.TransactionType.B2C,
        )
        callback_body = {
            'Result': {
                'ConversationID': mpesa_transaction.conversation_id,
                'ResultCode': 0,
                'ResultDesc': 'Success',
            },
        }

        response = self.client.post(
            reverse('payments:mpesa-b2c-callback'),
            callback_body,
            format='json',
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_503_SERVICE_UNAVAILABLE,
        )
        callback = Callback.objects.get()
        self.assertFalse(callback.processed)
        self.assertEqual(callback.transaction, transaction)
        self.assertEqual(callback.raw_payload['callback_type'], 'B2C')
        self.assertEqual(callback.raw_payload['payload'], callback_body)
        self.assertIn('broker unavailable', callback.processing_error)


class PaymentTaskHardeningTests(TestCase):
    """Regression tests for payment callback accounting hardening."""

    def setUp(self):
        self.user = User.objects.create_user(
            email='payment-hardening-member@example.com',
            phone_number='254712100001',
            password='StrongPass1',
        )
        self.admin = User.objects.create_user(
            email='payment-hardening-admin@example.com',
            phone_number='254712100002',
            password='StrongPass1',
        )
        self.sacco = Sacco.objects.create(
            name='Payment Hardening SACCO',
            registration_number='PAY-HARD-001',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
            membership_type=Sacco.MembershipType.OPEN,
        )
        self.membership = Membership.objects.create(
            user=self.user,
            sacco=self.sacco,
            status=Membership.Status.APPROVED,
            member_number='PAY-HARD-M-001',
        )
        Role.objects.create(
            user=self.admin,
            sacco=self.sacco,
            name=Role.SACCO_ADMIN,
        )
        self.savings_type = SavingsType.objects.create(
            sacco=self.sacco,
            name=SavingsType.Name.BOSA,
            minimum_contribution=Decimal('100.00'),
        )
        self.saving = Saving.objects.create(
            membership=self.membership,
            savings_type=self.savings_type,
            amount=Decimal('100.00'),
            total_contributions=Decimal('100.00'),
            status=Saving.Status.ACTIVE,
        )
        self.provider = PaymentProvider.objects.create(
            name='M-Pesa',
            provider_type=PaymentProvider.ProviderType.MPESA,
            is_active=True,
        )
        self.loan_type = LoanType.objects.create(
            sacco=self.sacco,
            name='Payment Hardening Loan',
            interest_rate=Decimal('12.00'),
            max_term_months=12,
            min_amount=Decimal('100.00'),
        )
        self.loan = Loan.objects.create(
            membership=self.membership,
            loan_type=self.loan_type,
            amount=Decimal('300.00'),
            interest_rate=Decimal('12.00'),
            term_months=3,
            outstanding_balance=Decimal('300.00'),
            status=Loan.Status.ACTIVE,
        )

    def _transaction(self, amount, transaction_type, reference):
        return Transaction.objects.create(
            provider=self.provider,
            user=self.user,
            reference=reference,
            transaction_type=transaction_type,
            amount=amount,
            sacco=self.sacco,
            status=Transaction.Status.PENDING,
            description='Payment hardening test',
        )

    def _mpesa_for_saving(self, transaction):
        return MpesaTransaction.objects.create(
            transaction=transaction,
            phone_number='254712100001',
            checkout_request_id=f'CHECKOUT-{transaction.reference}',
            mpesa_receipt_number=f'RCT-{transaction.reference}',
            related_saving=self.saving,
        )

    def _mpesa_for_loan(self, transaction, instalment_number=1):
        return MpesaTransaction.objects.create(
            transaction=transaction,
            phone_number='254712100001',
            checkout_request_id=f'CHECKOUT-{transaction.reference}',
            related_loan=self.loan,
            related_instalment_number=instalment_number,
        )

    def _create_instalments(self):
        due_date = timezone.localdate() + timedelta(days=30)
        for number in range(1, 4):
            RepaymentSchedule.objects.create(
                loan=self.loan,
                instalment_number=number,
                due_date=due_date + timedelta(days=30 * (number - 1)),
                amount=Decimal('100.00'),
                principal=Decimal('90.00'),
                interest=Decimal('10.00'),
                balance_after=Decimal('300.00') - (
                    Decimal('100.00') * number
                ),
            )

    @patch('ledger.utils.LedgerEntry.objects.create')
    def test_saving_deposit_rolls_back_when_ledger_write_fails(
        self,
        create_mock,
    ):
        create_mock.side_effect = RuntimeError('ledger unavailable')
        transaction = self._transaction(
            Decimal('25.00'),
            Transaction.TransactionType.DEPOSIT,
            'PAY-HARD-DEP-001',
        )
        mpesa_transaction = self._mpesa_for_saving(transaction)

        with self.assertRaises(RuntimeError):
            _apply_saving_deposit(
                mpesa_transaction,
                transaction,
                Decimal('25.00'),
            )

        self.saving.refresh_from_db()
        self.assertEqual(self.saving.amount, Decimal('100.00'))
        self.assertEqual(
            self.saving.total_contributions,
            Decimal('100.00'),
        )
        self.assertFalse(LedgerEntry.objects.exists())

    def test_loan_repayment_marks_underpaid_instalment_partial(self):
        self._create_instalments()
        transaction = self._transaction(
            Decimal('40.00'),
            Transaction.TransactionType.LOAN_REPAYMENT,
            'PAY-HARD-REP-001',
        )
        mpesa_transaction = self._mpesa_for_loan(transaction)

        _apply_loan_repayment(
            mpesa_transaction,
            transaction,
            Decimal('40.00'),
        )

        first = RepaymentSchedule.objects.get(instalment_number=1)
        self.loan.refresh_from_db()
        self.assertEqual(first.status, RepaymentSchedule.Status.PARTIAL)
        self.assertEqual(first.paid_amount, Decimal('40.00'))
        self.assertEqual(self.loan.outstanding_balance, Decimal('260.00'))

    def test_loan_repayment_carries_remainder_to_next_instalments(self):
        self._create_instalments()
        transaction = self._transaction(
            Decimal('250.00'),
            Transaction.TransactionType.LOAN_REPAYMENT,
            'PAY-HARD-REP-002',
        )
        mpesa_transaction = self._mpesa_for_loan(transaction)

        _apply_loan_repayment(
            mpesa_transaction,
            transaction,
            Decimal('250.00'),
        )

        first = RepaymentSchedule.objects.get(instalment_number=1)
        second = RepaymentSchedule.objects.get(instalment_number=2)
        third = RepaymentSchedule.objects.get(instalment_number=3)
        self.loan.refresh_from_db()
        self.assertEqual(first.status, RepaymentSchedule.Status.PAID)
        self.assertEqual(second.status, RepaymentSchedule.Status.PAID)
        self.assertEqual(third.status, RepaymentSchedule.Status.PARTIAL)
        self.assertEqual(third.paid_amount, Decimal('50.00'))
        self.assertEqual(self.loan.outstanding_balance, Decimal('50.00'))

    def test_amount_mismatch_does_not_credit_saving_and_notifies_admin(self):
        transaction = self._transaction(
            Decimal('100.00'),
            Transaction.TransactionType.DEPOSIT,
            'PAY-HARD-MISMATCH-001',
        )
        mpesa_transaction = self._mpesa_for_saving(transaction)
        stk_callback = {
            'ResultCode': 0,
            'ResultDesc': 'Success',
            'CallbackMetadata': {
                'Item': [
                    {'Name': 'Amount', 'Value': '90.00'},
                    {'Name': 'MpesaReceiptNumber', 'Value': 'MISMATCH001'},
                ],
            },
        }

        _process_successful_callback(
            mpesa_transaction,
            transaction,
            stk_callback,
        )

        transaction.refresh_from_db()
        mpesa_transaction.refresh_from_db()
        self.saving.refresh_from_db()
        self.assertEqual(
            transaction.status,
            Transaction.Status.AMOUNT_MISMATCH,
        )
        self.assertTrue(mpesa_transaction.callback_received)
        self.assertEqual(self.saving.amount, Decimal('100.00'))
        self.assertFalse(LedgerEntry.objects.exists())
        self.assertTrue(
            Notification.objects.filter(
                user=self.admin,
                title='Payment amount mismatch',
            ).exists()
        )

    def test_stk_callback_duplicate_delivery_does_not_double_credit(self):
        transaction = self._transaction(
            Decimal('25.00'),
            Transaction.TransactionType.DEPOSIT,
            'PAY-HARD-IDEMPOTENT-STK-001',
        )
        mpesa_transaction = self._mpesa_for_saving(transaction)
        callback_body = {
            'Body': {
                'stkCallback': {
                    'CheckoutRequestID': (
                        mpesa_transaction.checkout_request_id
                    ),
                    'ResultCode': 0,
                    'ResultDesc': 'Success',
                    'CallbackMetadata': {
                        'Item': [
                            {'Name': 'Amount', 'Value': '25.00'},
                            {
                                'Name': 'MpesaReceiptNumber',
                                'Value': 'IDEMPSTK001',
                            },
                        ],
                    },
                },
            },
        }

        process_stk_callback_task(
            mpesa_transaction.checkout_request_id,
            0,
            callback_body,
        )
        process_stk_callback_task(
            mpesa_transaction.checkout_request_id,
            0,
            callback_body,
        )

        self.saving.refresh_from_db()
        transaction.refresh_from_db()
        mpesa_transaction.refresh_from_db()
        self.assertEqual(transaction.status, Transaction.Status.COMPLETED)
        self.assertTrue(mpesa_transaction.callback_received)
        self.assertEqual(self.saving.amount, Decimal('125.00'))
        self.assertEqual(
            self.saving.total_contributions,
            Decimal('125.00'),
        )
        self.assertEqual(
            LedgerEntry.objects.filter(transaction=transaction).count(),
            1,
        )

    def test_generic_deposit_callback_records_net_ledger_and_invoice_fee(self):
        transaction = self._transaction(
            Decimal('1000.00'),
            Transaction.TransactionType.DEPOSIT,
            'PAY-HARD-GENERIC-DEP-001',
        )
        transaction.gross_amount = Decimal('1010.00')
        transaction.platform_fee = Decimal('10.00')
        transaction.fee_rate = Decimal('0.010000')
        transaction.save(
            update_fields=[
                'gross_amount',
                'platform_fee',
                'fee_rate',
                'updated_at',
            ]
        )

        _create_callback_ledger_entry(transaction)
        _record_platform_fee_for_sacco(transaction, self.sacco)

        ledger_entry = LedgerEntry.objects.get(transaction=transaction)
        line_item = InvoiceLineItem.objects.get(transaction=transaction)
        self.assertEqual(ledger_entry.entry_type, LedgerEntry.EntryType.CREDIT)
        self.assertEqual(ledger_entry.amount, Decimal('1000.00'))
        self.assertIsInstance(ledger_entry.description, str)
        self.assertNotIn("('", ledger_entry.description)
        self.assertEqual(line_item.platform_fee, Decimal('10.00'))
        self.assertEqual(line_item.gross_amount, Decimal('1010.00'))
        self.assertEqual(line_item.net_amount, Decimal('1000.00'))
        self.assertEqual(line_item.billing_month.day, 1)

    def test_generic_withdrawal_callback_debits_gross_and_invoices_fee(self):
        transaction = self._transaction(
            Decimal('4975.00'),
            Transaction.TransactionType.WITHDRAWAL,
            'PAY-HARD-GENERIC-WD-001',
        )
        transaction.gross_amount = Decimal('5000.00')
        transaction.platform_fee = Decimal('25.00')
        transaction.save(
            update_fields=[
                'gross_amount',
                'platform_fee',
                'updated_at',
            ]
        )

        _create_callback_ledger_entry(transaction)
        _record_platform_fee_for_sacco(transaction, self.sacco)

        ledger_entry = LedgerEntry.objects.get(transaction=transaction)
        line_item = InvoiceLineItem.objects.get(transaction=transaction)
        self.assertEqual(ledger_entry.entry_type, LedgerEntry.EntryType.DEBIT)
        self.assertEqual(ledger_entry.amount, Decimal('5000.00'))
        self.assertEqual(line_item.platform_fee, Decimal('25.00'))
        self.assertEqual(line_item.gross_amount, Decimal('5000.00'))
        self.assertEqual(line_item.net_amount, Decimal('4975.00'))
        self.assertEqual(line_item.fee_model, 'tiered_flat')

    def test_disbursement_is_not_invoiced_by_generic_callback_helper(self):
        transaction = self._transaction(
            Decimal('100000.00'),
            Transaction.TransactionType.LOAN_DISBURSEMENT,
            'PAY-HARD-GENERIC-DISB-001',
        )

        _record_platform_fee_for_sacco(transaction, self.sacco)

        self.assertFalse(
            InvoiceLineItem.objects.filter(transaction=transaction).exists()
        )


class B2CDisbursementHardeningTests(TestCase):
    """Regression tests for local-first B2C attempt recording."""

    def setUp(self):
        self.user = User.objects.create_user(
            email='b2c-hardening-member@example.com',
            phone_number='254712200001',
            password='StrongPass1',
        )
        self.sacco = Sacco.objects.create(
            name='B2C Hardening SACCO',
            registration_number='B2C-HARD-001',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
            membership_type=Sacco.MembershipType.OPEN,
        )
        self.membership = Membership.objects.create(
            user=self.user,
            sacco=self.sacco,
            status=Membership.Status.APPROVED,
            member_number='B2C-HARD-M-001',
        )
        loan_type = LoanType.objects.create(
            sacco=self.sacco,
            name='B2C Hardening Loan',
            interest_rate=Decimal('12.00'),
            max_term_months=12,
            min_amount=Decimal('100.00'),
        )
        self.loan = Loan.objects.create(
            membership=self.membership,
            loan_type=loan_type,
            amount=Decimal('500.00'),
            interest_rate=Decimal('12.00'),
            term_months=6,
            outstanding_balance=Decimal('0.00'),
            status=Loan.Status.APPROVED,
        )
        self.provider = PaymentProvider.objects.create(
            name='M-Pesa',
            provider_type=PaymentProvider.ProviderType.MPESA,
            is_active=True,
        )

    @patch('payments.disbursements.DarajaClient')
    def test_b2c_api_failure_leaves_failed_local_attempt(
        self,
        client_mock,
    ):
        client = client_mock.return_value
        client._build_callback_url.return_value = 'https://callback.test/b2c'
        client.initiate_b2c.side_effect = DarajaError(
            'Daraja unavailable',
            '500.001',
        )

        success, payload, http_status = initiate_b2c_loan_disbursement(
            loan=self.loan,
            phone_number='254712200001',
            amount=Decimal('500.00'),
            remarks='Loan Disbursement',
        )

        self.assertFalse(success)
        self.assertEqual(http_status, 502)
        self.assertEqual(payload['error'], 'Daraja unavailable')
        transaction = Transaction.objects.get(
            transaction_type=Transaction.TransactionType.LOAN_DISBURSEMENT,
        )
        mpesa_transaction = MpesaTransaction.objects.get(
            transaction=transaction,
        )
        self.loan.refresh_from_db()
        self.assertEqual(transaction.status, Transaction.Status.FAILED)
        self.assertIsNone(mpesa_transaction.conversation_id)
        self.assertEqual(self.loan.status, Loan.Status.APPROVED)

    def test_b2c_callback_duplicate_delivery_does_not_double_disburse(self):
        transaction = Transaction.objects.create(
            provider=self.provider,
            user=self.user,
            reference='B2C-HARD-IDEMPOTENT-001',
            transaction_type=Transaction.TransactionType.LOAN_DISBURSEMENT,
            amount=Decimal('500.00'),
            status=Transaction.Status.SENT,
            description='B2C idempotency test',
        )
        mpesa_transaction = MpesaTransaction.objects.create(
            transaction=transaction,
            phone_number='254712200001',
            conversation_id='B2C-CONVERSATION-IDEMPOTENT-001',
            transaction_type=MpesaTransaction.TransactionType.B2C,
            related_loan=self.loan,
        )
        callback_body = {
            'Result': {
                'ConversationID': mpesa_transaction.conversation_id,
                'ResultCode': 0,
                'ResultDesc': 'Success',
                'ResultParameters': {
                    'ResultParameter': [
                        {
                            'Key': 'TransactionReceipt',
                            'Value': 'B2CIDEMP001',
                        },
                    ],
                },
            },
        }

        process_b2c_callback_task(
            mpesa_transaction.conversation_id,
            0,
            callback_body,
        )
        process_b2c_callback_task(
            mpesa_transaction.conversation_id,
            0,
            callback_body,
        )

        self.loan.refresh_from_db()
        transaction.refresh_from_db()
        mpesa_transaction.refresh_from_db()
        self.assertEqual(transaction.status, Transaction.Status.COMPLETED)
        self.assertTrue(mpesa_transaction.callback_received)
        self.assertEqual(self.loan.status, Loan.Status.ACTIVE)
        self.assertEqual(self.loan.disbursed_amount, Decimal('500.00'))
        self.assertEqual(self.loan.outstanding_balance, Decimal('500.00'))
        self.assertEqual(
            LedgerEntry.objects.filter(transaction=transaction).count(),
            1,
        )
