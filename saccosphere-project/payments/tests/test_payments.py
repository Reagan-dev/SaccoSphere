import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.db import connection
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from kombu.exceptions import OperationalError as KombuOperationalError
from requests.exceptions import Timeout as RequestsTimeout
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models import Sacco, SaccoPaymentConfig, User
from billing.models import InvoiceLineItem
from ledger.models import LedgerEntry
from notifications.models import Notification
from payments.disbursements import initiate_b2c_loan_disbursement
from payments.models import (
    Callback,
    MpesaIdempotencyRecord,
    MpesaTransaction,
    PaymentProvider,
    Transaction,
)
from payments.tasks import (
    _apply_loan_repayment,
    _apply_saving_deposit,
    _create_callback_ledger_entry,
    _process_successful_callback,
    _reconcile_stale_b2c_disbursements,
    _record_platform_fee_for_sacco,
    process_b2c_callback_task,
    process_stk_callback_task,
)
from saccomanagement.models import Role
from saccomembership.models import Membership
from services.models import (
    DisbursementAuditLog,
    Guarantor,
    Loan,
    LoanType,
    RepaymentSchedule,
    Saving,
    SavingsType,
)

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

    def test_deposit_endpoint_deprecated(self):
        """Deposit endpoint is deprecated and returns 410 Gone."""
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

        self.assertEqual(response.status_code, status.HTTP_410_GONE)
        self.assertIn('deprecated', response.data['detail'].lower())
        self.assertIn('alternative', response.data)


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

    def test_withdrawal_endpoint_deprecated(self):
        """Withdrawal endpoint is deprecated and returns 410 Gone."""
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

        self.assertEqual(response.status_code, status.HTTP_410_GONE)
        self.assertIn('deprecated', response.data['detail'].lower())
        self.assertIn('alternative', response.data)


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
    """Validate generic PSP callback endpoint is deprecated."""

    def setUp(self):
        self.client = APIClient()

    def test_callback_create_deprecated(self):
        """Callback create endpoint is deprecated and returns 410 Gone."""
        response = self.client.post(
            reverse('payments:callback-create'),
            {
                'raw_payload': {'transaction_id': 'txn-001'},
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_410_GONE)
        self.assertIn('deprecated', response.data['detail'].lower())
        self.assertIn('alternatives', response.data)


class PaymentCallbackViewTests(TestCase):
    """Validate generic payment callback endpoint is deprecated."""

    def setUp(self):
        self.client = APIClient()

    def test_payment_callback_deprecated(self):
        """Payment callback endpoint is deprecated and returns 410 Gone."""
        response = self.client.post(
            reverse('payments:payment-callback'),
            {},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_410_GONE)
        self.assertIn('deprecated', response.data['detail'].lower())
        self.assertIn('alternatives', response.data)


class STKPushInitiationHardeningTests(TestCase):
    """Regression tests for local-first STK initiation recording."""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            email='stk.hardening@example.com',
            phone_number='254712900001',
            password='StrongPass1',
        )
        self.sacco = Sacco.objects.create(
            name='STK Hardening SACCO',
            registration_number='STK-HARD-001',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
            membership_type=Sacco.MembershipType.OPEN,
        )
        self.membership = Membership.objects.create(
            user=self.user,
            sacco=self.sacco,
            status=Membership.Status.APPROVED,
            member_number='STK-HARD-M-001',
        )
        self.savings_type = SavingsType.objects.create(
            sacco=self.sacco,
            name=SavingsType.Name.BOSA,
            minimum_contribution=Decimal('100.00'),
        )
        self.saving = Saving.objects.create(
            membership=self.membership,
            savings_type=self.savings_type,
            amount=Decimal('1000.00'),
            total_contributions=Decimal('1000.00'),
            status=Saving.Status.ACTIVE,
        )

    def _payload(self):
        return {
            'phone_number': '254712900001',
            'amount': '1000.00',
            'purpose': 'SAVING_DEPOSIT',
            'sacco_id': str(self.sacco.id),
            'saving_id': str(self.saving.id),
        }

    @patch('payments.views.DarajaClient.initiate_stk_push')
    def test_stk_attempt_is_recorded_before_daraja_call(self, stk_mock):
        self.client.force_authenticate(user=self.user)

        def fake_stk_push(**_kwargs):
            transaction = Transaction.objects.get()
            mpesa_transaction = MpesaTransaction.objects.get(
                transaction=transaction,
            )
            self.assertEqual(transaction.status, Transaction.Status.PENDING)
            self.assertIsNone(transaction.external_reference)
            self.assertIsNone(mpesa_transaction.checkout_request_id)
            return {
                'ResponseCode': '0',
                'MerchantRequestID': 'MRID-STK-HARD-001',
                'CheckoutRequestID': 'CRID-STK-HARD-001',
            }

        stk_mock.side_effect = fake_stk_push

        response = self.client.post(
            reverse('payments:mpesa-stk-push'),
            self._payload(),
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        transaction = Transaction.objects.get()
        mpesa_transaction = MpesaTransaction.objects.get(
            transaction=transaction,
        )
        self.assertEqual(
            transaction.external_reference,
            'CRID-STK-HARD-001',
        )
        self.assertEqual(
            transaction.metadata['initiation_status'],
            'ACCEPTED',
        )
        self.assertEqual(
            mpesa_transaction.merchant_request_id,
            'MRID-STK-HARD-001',
        )
        self.assertEqual(
            mpesa_transaction.checkout_request_id,
            'CRID-STK-HARD-001',
        )

    @patch('payments.views.DarajaClient.initiate_stk_push')
    def test_daraja_error_marks_local_attempt_failed(self, stk_mock):
        self.client.force_authenticate(user=self.user)
        stk_mock.side_effect = DarajaError('Rejected by M-Pesa', '1')

        response = self.client.post(
            reverse('payments:mpesa-stk-push'),
            self._payload(),
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_502_BAD_GATEWAY)
        transaction = Transaction.objects.get()
        mpesa_transaction = MpesaTransaction.objects.get(
            transaction=transaction,
        )
        self.assertEqual(
            transaction.status,
            Transaction.Status.INITIATION_FAILED,
        )
        self.assertEqual(transaction.metadata['initiation_status'], 'FAILED')
        self.assertEqual(mpesa_transaction.result_code, '1')
        self.assertEqual(
            mpesa_transaction.result_description,
            'Rejected by M-Pesa',
        )

    @patch('payments.views.DarajaClient.initiate_stk_push')
    def test_daraja_timeout_surfaces_unknown_recorded_status(self, stk_mock):
        self.client.force_authenticate(user=self.user)

        def raise_timeout(**_kwargs):
            try:
                raise RequestsTimeout('read timed out')
            except RequestsTimeout as exc:
                raise DarajaError(
                    'M-Pesa request timed out. Please try again.',
                ) from exc

        stk_mock.side_effect = raise_timeout

        response = self.client.post(
            reverse('payments:mpesa-stk-push'),
            self._payload(),
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        transaction = Transaction.objects.get()
        self.assertEqual(
            transaction.status,
            Transaction.Status.INITIATION_FAILED,
        )
        self.assertEqual(transaction.metadata['initiation_status'], 'UNKNOWN')
        self.assertTrue(
            transaction.metadata['initiation_error']['status_unknown'],
        )
        self.assertIn('will be reconciled', response.data['detail'])

    @patch('payments.views.DarajaClient.initiate_stk_push')
    def test_duplicate_stk_push_reuses_existing_local_attempt(self, stk_mock):
        self.client.force_authenticate(user=self.user)
        provider = PaymentProvider.objects.create(
            name='M-Pesa',
            provider_type=PaymentProvider.ProviderType.MPESA,
            is_active=True,
        )
        transaction = Transaction.objects.create(
            provider=provider,
            user=self.user,
            reference='STK-HARD-DUPLICATE-001',
            external_reference='CRID-STK-HARD-DUPLICATE',
            transaction_type=Transaction.TransactionType.DEPOSIT,
            amount=Decimal('1000.00'),
            gross_amount=Decimal('1010.00'),
            platform_fee=Decimal('10.00'),
            fee_rate=Decimal('0.010000'),
            sacco=self.sacco,
            fee_amount=Decimal('10.00'),
            status=Transaction.Status.PENDING,
            description='SaccoSphere saving deposit',
        )
        MpesaTransaction.objects.create(
            transaction=transaction,
            phone_number='254712900001',
            merchant_request_id='MRID-STK-HARD-DUPLICATE',
            checkout_request_id='CRID-STK-HARD-DUPLICATE',
            related_saving=self.saving,
        )

        response = self.client.post(
            reverse('payments:mpesa-stk-push'),
            self._payload(),
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['duplicate'])
        self.assertEqual(
            response.data['checkout_request_id'],
            'CRID-STK-HARD-DUPLICATE',
        )
        self.assertEqual(Transaction.objects.count(), 1)
        stk_mock.assert_not_called()


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

    def test_overpayment_past_final_instalment_books_refund_liability(self):
        self._create_instalments()  # 3 x KES 100 = KES 300 due
        transaction = self._transaction(
            Decimal('350.00'),
            Transaction.TransactionType.LOAN_REPAYMENT,
            'PAY-HARD-OVERPAY-001',
        )
        transaction.gross_amount = Decimal('350.00')
        transaction.save(update_fields=['gross_amount', 'updated_at'])
        mpesa_transaction = self._mpesa_for_loan(transaction)

        _apply_loan_repayment(
            mpesa_transaction,
            transaction,
            Decimal('350.00'),
        )

        self.loan.refresh_from_db()
        transaction.refresh_from_db()
        # KES 300 clears the schedule; KES 50 is the overpayment.
        self.assertEqual(self.loan.outstanding_balance, Decimal('0.00'))
        self.assertEqual(
            RepaymentSchedule.objects.filter(
                loan=self.loan,
                status=RepaymentSchedule.Status.PAID,
            ).count(),
            3,
        )

        repayment_entry = LedgerEntry.objects.get(
            reference=str(transaction.id),
        )
        self.assertEqual(
            repayment_entry.category,
            LedgerEntry.Category.LOAN_REPAYMENT,
        )
        self.assertEqual(repayment_entry.amount, Decimal('300.00'))

        overpay_entry = LedgerEntry.objects.get(
            reference=f'{transaction.id}-OVERPAY',
        )
        self.assertEqual(
            overpay_entry.entry_type, LedgerEntry.EntryType.CREDIT,
        )
        self.assertEqual(
            overpay_entry.category, LedgerEntry.Category.ADJUSTMENT,
        )
        self.assertEqual(overpay_entry.amount, Decimal('50.00'))

        self.assertEqual(
            transaction.metadata['overpayment']['status'],
            'PENDING_REFUND',
        )
        self.assertEqual(
            transaction.metadata['overpayment']['amount'], '50.00',
        )
        self.assertTrue(
            Notification.objects.filter(
                user=self.admin,
                title='Loan overpayment - refund due',
            ).exists()
        )

    def test_full_overpayment_on_settled_loan_still_books_liability(self):
        # No unpaid instalments: nothing applies, all of it is overpayment.
        transaction = self._transaction(
            Decimal('75.00'),
            Transaction.TransactionType.LOAN_REPAYMENT,
            'PAY-HARD-OVERPAY-002',
        )
        transaction.gross_amount = Decimal('75.00')
        transaction.save(update_fields=['gross_amount', 'updated_at'])
        mpesa_transaction = self._mpesa_for_loan(transaction)
        balance_before = self.loan.outstanding_balance

        _apply_loan_repayment(
            mpesa_transaction,
            transaction,
            Decimal('75.00'),
        )

        self.loan.refresh_from_db()
        self.assertEqual(self.loan.outstanding_balance, balance_before)
        self.assertFalse(
            LedgerEntry.objects.filter(
                reference=str(transaction.id),
            ).exists()
        )
        overpay_entry = LedgerEntry.objects.get(
            reference=f'{transaction.id}-OVERPAY',
        )
        self.assertEqual(overpay_entry.amount, Decimal('75.00'))

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

        first_callback = Callback.objects.create(
            transaction=transaction,
            provider=self.provider,
            raw_payload=callback_body,
        )
        second_callback = Callback.objects.create(
            transaction=transaction,
            provider=self.provider,
            raw_payload=callback_body,
        )

        process_stk_callback_task(str(first_callback.id))
        process_stk_callback_task(str(second_callback.id))

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
            payment_ready=True,
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
            disbursement_status=Loan.DisbursementStatus.PENDING,
        )
        self.provider = PaymentProvider.objects.create(
            name='M-Pesa',
            provider_type=PaymentProvider.ProviderType.MPESA,
            is_active=True,
        )
        self.payment_config = SaccoPaymentConfig.objects.create(
            sacco=self.sacco,
            shortcode_type=SaccoPaymentConfig.ShortcodeType.PAYBILL,
            shortcode='600999',
            stk_passkey='hardening_passkey',
            daraja_consumer_key='hardening_consumer_key',
            daraja_consumer_secret='hardening_consumer_secret',
            environment=SaccoPaymentConfig.Environment.SANDBOX,
            b2c_initiator_name='hardening_initiator',
            b2c_security_credential='hardening_security_credential',
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
            phone_number='+254712200001',
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

    @patch('payments.disbursements.DarajaClient')
    def test_b2c_disbursement_blocked_by_pending_guarantors(
        self,
        client_mock,
    ):
        """Disbursement should fail when loan has pending guarantor approvals."""
        client = client_mock.return_value
        client._build_callback_url.return_value = 'https://callback.test/b2c'

        # Create a pending guarantor
        guarantor_user = User.objects.create_user(
            email='guarantor@test.com',
            phone_number='254712200002',
        )
        Guarantor.objects.create(
            loan=self.loan,
            guarantor=guarantor_user,
            status=Guarantor.Status.PENDING,
            guarantee_amount=Decimal('250.00'),
        )

        success, payload, http_status = initiate_b2c_loan_disbursement(
            loan=self.loan,
            phone_number='254712200001',
            amount=Decimal('500.00'),
            remarks='Loan Disbursement',
        )

        self.assertFalse(success)
        self.assertEqual(http_status, 400)
        self.assertIn(
            'pending guarantor approvals',
            payload['error'],
        )
        # Verify Daraja was never called
        client.initiate_b2c.assert_not_called()

    def test_b2c_disbursement_proceeds_with_approved_guarantors(self):
        """Disbursement should succeed when all guarantors are approved."""
        # Create an approved guarantor
        guarantor_user = User.objects.create_user(
            email='guarantor@test.com',
            phone_number='254712200002',
        )
        Guarantor.objects.create(
            loan=self.loan,
            guarantor=guarantor_user,
            status=Guarantor.Status.APPROVED,
            guarantee_amount=Decimal('250.00'),
        )

        # Check that no pending guarantors exist
        pending_count = self.loan.guarantors.filter(
            status=Guarantor.Status.PENDING
        ).count()
        self.assertEqual(pending_count, 0)

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

        first_callback = Callback.objects.create(
            transaction=transaction,
            provider=self.provider,
            raw_payload=callback_body,
        )
        second_callback = Callback.objects.create(
            transaction=transaction,
            provider=self.provider,
            raw_payload=callback_body,
        )

        process_b2c_callback_task(str(first_callback.id))
        process_b2c_callback_task(str(second_callback.id))

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


class B2CCallbackConcurrencyRegressionTests(TransactionTestCase):
    """Cross-connection race test for the 'no double-disbursement' guarantee.

    ``B2CDisbursementHardeningTests`` extends ``django.test.TestCase`` and its
    ``test_b2c_callback_duplicate_delivery_does_not_double_disburse`` invokes
    ``process_b2c_callback_task`` twice *sequentially* on a single connection
    inside the test's outer transaction. That proves sequential idempotency but
    can never exercise the actual failure mode this safety net exists for: two
    Safaricom B2C result callbacks for the same ``ConversationID`` being
    processed at the same instant on two different DB connections/workers.

    This class exercises that race directly - two threads, each with its own
    connection, released together on a barrier - and asserts the loan is
    disbursed exactly once.

    Requires PostgreSQL, which is the production backend on Railway. On SQLite
    ``select_for_update()`` is a silent no-op (so the row lock in
    ``process_b2c_callback_task`` provides nothing) and concurrent writers just
    raise ``database is locked``, so the race cannot be represented faithfully.
    The test skips there, matching
    ``accounts.tests.test_otp_security.OTPRaceConditionTestCase``.
    """

    def setUp(self):
        self.user = User.objects.create_user(
            email='b2c-race-member@example.com',
            phone_number='254712260001',
            password='StrongPass1',
        )
        self.sacco = Sacco.objects.create(
            name='B2C Race SACCO',
            registration_number='B2C-RACE-001',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
            membership_type=Sacco.MembershipType.OPEN,
            payment_ready=True,
        )
        self.membership = Membership.objects.create(
            user=self.user,
            sacco=self.sacco,
            status=Membership.Status.APPROVED,
            member_number='B2C-RACE-M-001',
        )
        loan_type = LoanType.objects.create(
            sacco=self.sacco,
            name='B2C Race Loan',
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
            status=Loan.Status.DISBURSEMENT_PENDING,
            disbursement_status=Loan.DisbursementStatus.INITIATED,
        )
        self.provider = PaymentProvider.objects.create(
            name='M-Pesa',
            provider_type=PaymentProvider.ProviderType.MPESA,
            is_active=True,
        )

    def test_concurrent_b2c_callbacks_disburse_loan_exactly_once(self):
        if connection.vendor == 'sqlite':
            self.skipTest(
                'SQLite has no real row-level locking (select_for_update is a '
                'no-op) and serialises writers with "database is locked"; run '
                'against PostgreSQL to exercise this race.'
            )

        transaction = Transaction.objects.create(
            provider=self.provider,
            user=self.user,
            reference='B2C-RACE-IDEMPOTENT-001',
            transaction_type=Transaction.TransactionType.LOAN_DISBURSEMENT,
            amount=Decimal('500.00'),
            status=Transaction.Status.SENT,
            description='B2C concurrency regression test',
        )
        mpesa_transaction = MpesaTransaction.objects.create(
            transaction=transaction,
            phone_number='254712260001',
            conversation_id='B2C-CONVERSATION-RACE-001',
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
                        {'Key': 'TransactionReceipt', 'Value': 'B2CRACE001'},
                    ],
                },
            },
        }
        callback_ids = [
            str(
                Callback.objects.create(
                    transaction=transaction,
                    provider=self.provider,
                    raw_payload=callback_body,
                ).id
            )
            for _ in range(2)
        ]

        barrier = threading.Barrier(len(callback_ids))
        errors = []

        def deliver(callback_id):
            barrier.wait()
            try:
                process_b2c_callback_task(callback_id)
            except Exception as exc:  # noqa: BLE001 - recorded and asserted on
                errors.append(exc)
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=len(callback_ids)) as executor:
            list(executor.map(deliver, callback_ids))

        # A losing thread may raise (retry/IntegrityError) - that is the guard
        # doing its job, not a failure - but it must never be that both threads
        # applied the disbursement.
        self.assertLessEqual(
            len(errors), 1, f'Both callbacks errored: {errors}',
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
            'Loan disbursement credited more than once under concurrent '
            'callback delivery.',
        )
        self.assertEqual(
            MpesaIdempotencyRecord.objects.filter(
                checkout_request_id=mpesa_transaction.conversation_id,
            ).count(),
            1,
        )


def _make_b2c_ready_sacco(registration_number, member_email, phone_number):
    """Build a payment-ready SACCO + approved member + PENDING loan."""
    sacco = Sacco.objects.create(
        name=f'B2C Init {registration_number}',
        registration_number=registration_number,
        sector=Sacco.Sector.FINANCE,
        county='Nairobi',
        membership_type=Sacco.MembershipType.OPEN,
        payment_ready=True,
    )
    SaccoPaymentConfig.objects.create(
        sacco=sacco,
        shortcode_type=SaccoPaymentConfig.ShortcodeType.PAYBILL,
        shortcode='600111',
        stk_passkey='init_passkey',
        daraja_consumer_key='init_consumer_key',
        daraja_consumer_secret='init_consumer_secret',
        environment=SaccoPaymentConfig.Environment.SANDBOX,
        b2c_initiator_name='init_initiator',
        b2c_security_credential='init_security_credential',
        is_active=True,
    )
    user = User.objects.create_user(
        email=member_email,
        phone_number=phone_number,
        password='StrongPass1',
    )
    membership = Membership.objects.create(
        user=user,
        sacco=sacco,
        status=Membership.Status.APPROVED,
        member_number=f'{registration_number}-M-001',
    )
    loan_type = LoanType.objects.create(
        sacco=sacco,
        name=f'B2C Init Loan {registration_number}',
        interest_rate=Decimal('12.00'),
        max_term_months=12,
        min_amount=Decimal('100.00'),
    )
    loan = Loan.objects.create(
        membership=membership,
        loan_type=loan_type,
        amount=Decimal('500.00'),
        interest_rate=Decimal('12.00'),
        term_months=6,
        outstanding_balance=Decimal('0.00'),
        status=Loan.Status.APPROVED,
        disbursement_status=Loan.DisbursementStatus.PENDING,
    )
    return sacco, user, loan


class B2CDisbursementIdempotencyTests(TestCase):
    """Deterministic guards for initiate_b2c_loan_disbursement.

    Backend-agnostic: exercises the compare-and-swap claim, the duplicate
    rejection, and the timeout-vs-hard-failure split without threads.
    """

    def setUp(self):
        self.sacco, self.member, self.loan = _make_b2c_ready_sacco(
            'IDEMP01',
            'b2c-idemp-member@example.com',
            '254712230001',
        )

    def _call(self):
        from payments.disbursements import initiate_b2c_loan_disbursement

        return initiate_b2c_loan_disbursement(
            loan=self.loan,
            phone_number='+254712230001',
            amount=Decimal('500.00'),
            remarks='Loan Disbursement',
        )

    @patch('payments.disbursements.DarajaClient')
    def test_duplicate_request_is_rejected_without_second_daraja_call(
        self,
        client_mock,
    ):
        client = client_mock.return_value
        client._build_callback_url.return_value = 'https://cb.test/b2c'
        client.initiate_b2c.return_value = {
            'ConversationID': 'CONV-IDEMP-1',
            'OriginatorConversationID': 'ORIG-IDEMP-1',
        }

        ok, payload, http_status = self._call()
        self.assertTrue(ok)
        self.assertEqual(http_status, 201)
        self.loan.refresh_from_db()
        self.assertEqual(
            self.loan.disbursement_status,
            Loan.DisbursementStatus.INITIATED,
        )
        self.assertIsNotNone(self.loan.disbursement_idempotency_key)
        first_key = payload['idempotency_key']

        dup_ok, dup_payload, dup_status = self._call()

        self.assertFalse(dup_ok)
        self.assertEqual(dup_status, 409)
        self.assertEqual(client.initiate_b2c.call_count, 1)
        self.assertEqual(
            Transaction.objects.filter(
                transaction_type=(
                    Transaction.TransactionType.LOAN_DISBURSEMENT
                ),
            ).count(),
            1,
        )
        self.assertEqual(
            DisbursementAuditLog.objects.filter(
                loan=self.loan,
                event='B2C_INITIATED',
            ).count(),
            1,
        )
        self.assertEqual(
            dup_payload['idempotency_key'],
            first_key,
        )

    @patch('payments.disbursements.DarajaClient')
    def test_timeout_is_pending_confirmation_not_failed(self, client_mock):
        from payments.integrations.mpesa.daraja import DarajaError

        client = client_mock.return_value
        client._build_callback_url.return_value = 'https://cb.test/b2c'
        client.initiate_b2c.side_effect = DarajaError(
            'M-Pesa request timed out. Please try again.',
            is_timeout=True,
        )

        ok, payload, http_status = self._call()

        self.assertFalse(ok)
        self.assertEqual(http_status, 202)
        self.loan.refresh_from_db()
        self.assertEqual(
            self.loan.disbursement_status,
            Loan.DisbursementStatus.PENDING_CONFIRMATION,
        )
        payment = Transaction.objects.get(
            transaction_type=Transaction.TransactionType.LOAN_DISBURSEMENT,
        )
        self.assertEqual(
            payment.status,
            Transaction.Status.INITIATION_FAILED,
        )
        failed_rows = DisbursementAuditLog.objects.filter(
            loan=self.loan,
            event='DISBURSEMENT_FAILED',
        )
        self.assertEqual(failed_rows.count(), 1)
        self.assertEqual(
            failed_rows.get().details['outcome'],
            'timeout_ambiguous',
        )

    @patch('payments.disbursements.DarajaClient')
    def test_hard_daraja_error_is_failed_and_502(self, client_mock):
        from payments.integrations.mpesa.daraja import DarajaError

        client = client_mock.return_value
        client._build_callback_url.return_value = 'https://cb.test/b2c'
        client.initiate_b2c.side_effect = DarajaError(
            'Daraja unavailable',
            '500.001',
        )

        ok, payload, http_status = self._call()

        self.assertFalse(ok)
        self.assertEqual(http_status, 502)
        self.loan.refresh_from_db()
        self.assertEqual(
            self.loan.disbursement_status,
            Loan.DisbursementStatus.FAILED,
        )
        payment = Transaction.objects.get(
            transaction_type=Transaction.TransactionType.LOAN_DISBURSEMENT,
        )
        self.assertEqual(payment.status, Transaction.Status.FAILED)


class B2CInitiationConcurrencyRegressionTests(TransactionTestCase):
    """Two near-simultaneous B2C disbursement requests for one loan.

    The safety net this feature exists for: a duplicate/retried request
    or two racing admin clicks must never fire two real Safaricom
    payouts. Uses real threads and connections under TransactionTestCase
    because plain TestCase wraps the test in one transaction that hides
    the cross-connection race.

    Requires PostgreSQL (Railway's production backend). On SQLite
    select_for_update() is a no-op and concurrent writers raise
    'database is locked', so the race cannot be represented faithfully -
    skipped there, matching
    accounts.tests.test_otp_security.OTPRaceConditionTestCase. The
    deterministic guard is covered by B2CDisbursementIdempotencyTests on
    every backend.
    """

    def setUp(self):
        self.sacco, self.member, self.loan = _make_b2c_ready_sacco(
            'RACE01',
            'b2c-race-init-member@example.com',
            '254712240001',
        )

    def test_concurrent_requests_fire_exactly_one_daraja_call(self):
        if connection.vendor == 'sqlite':
            self.skipTest(
                'SQLite has no real row-level locking (select_for_update '
                'is a no-op) and serialises writers with "database is '
                'locked"; run against PostgreSQL to exercise this race.'
            )

        with patch('payments.disbursements.DarajaClient') as client_mock:
            client = client_mock.return_value
            client._build_callback_url.return_value = 'https://cb.test/b2c'
            client.initiate_b2c.return_value = {
                'ConversationID': 'CONV-RACE-1',
                'OriginatorConversationID': 'ORIG-RACE-1',
            }

            from payments.disbursements import (
                initiate_b2c_loan_disbursement,
            )

            barrier = threading.Barrier(2)
            results = []
            errors = []

            def fire():
                barrier.wait()
                try:
                    results.append(
                        initiate_b2c_loan_disbursement(
                            loan=self.loan,
                            phone_number='+254712240001',
                            amount=Decimal('500.00'),
                            remarks='Loan Disbursement',
                        )
                    )
                except Exception as exc:  # noqa: BLE001 - asserted below
                    errors.append(exc)
                finally:
                    connection.close()

            with ThreadPoolExecutor(max_workers=2) as executor:
                list(executor.map(lambda _: fire(), range(2)))

            self.assertEqual(errors, [])
            self.assertEqual(client.initiate_b2c.call_count, 1)

        statuses = sorted(http_status for _ok, _p, http_status in results)
        self.assertEqual(statuses, [201, 409])

        self.assertEqual(
            Transaction.objects.filter(
                transaction_type=(
                    Transaction.TransactionType.LOAN_DISBURSEMENT
                ),
            ).count(),
            1,
        )
        self.assertEqual(
            MpesaTransaction.objects.filter(
                transaction_type=MpesaTransaction.TransactionType.B2C,
            ).count(),
            1,
        )
        self.assertEqual(
            DisbursementAuditLog.objects.filter(loan=self.loan).count(),
            1,
        )
        self.assertEqual(
            DisbursementAuditLog.objects.get(loan=self.loan).event,
            'B2C_INITIATED',
        )
        self.loan.refresh_from_db()
        self.assertEqual(
            self.loan.disbursement_status,
            Loan.DisbursementStatus.INITIATED,
        )
        self.assertIsNotNone(self.loan.disbursement_idempotency_key)


class B2CReconciliationTests(TestCase):
    """Stale B2C disbursements (timeout) are escalated, never auto-retried."""

    def setUp(self):
        self.sacco, self.member, self.loan = _make_b2c_ready_sacco(
            'RECON01',
            'b2c-recon-member@example.com',
            '254712250001',
        )
        self.loan.disbursement_status = (
            Loan.DisbursementStatus.PENDING_CONFIRMATION
        )
        self.loan.disbursement_idempotency_key = (
            'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
        )
        self.loan.save(
            update_fields=[
                'disbursement_status',
                'disbursement_idempotency_key',
                'updated_at',
            ],
        )
        self.provider = PaymentProvider.objects.create(
            name='M-Pesa',
            provider_type=PaymentProvider.ProviderType.MPESA,
            is_active=True,
        )
        self.transaction = Transaction.objects.create(
            provider=self.provider,
            user=self.member,
            reference='SS-DSB-RECON01',
            transaction_type=Transaction.TransactionType.LOAN_DISBURSEMENT,
            amount=Decimal('500.00'),
            status=Transaction.Status.INITIATION_FAILED,
            description='B2C recon test',
        )
        self.mpesa = MpesaTransaction.objects.create(
            transaction=self.transaction,
            phone_number='254712250001',
            conversation_id='CONV-RECON-01',
            transaction_type=MpesaTransaction.TransactionType.B2C,
            related_loan=self.loan,
        )

    def _future_cutoff(self):
        return timezone.now() + timedelta(minutes=1)

    def test_escalates_to_under_review_after_max_attempts(self):
        escalated = _reconcile_stale_b2c_disbursements(
            self._future_cutoff(),
            max_attempts=1,
        )

        self.assertEqual(escalated, 1)
        self.loan.refresh_from_db()
        self.assertEqual(
            self.loan.disbursement_status,
            Loan.DisbursementStatus.UNDER_REVIEW,
        )
        rows = DisbursementAuditLog.objects.filter(
            loan=self.loan,
            event='ESCALATED_TO_SUPERADMIN',
        )
        self.assertEqual(rows.count(), 1)
        self.assertEqual(
            rows.get().details['reason'],
            'b2c_initiation_timeout_unconfirmed',
        )

    def test_below_max_attempts_only_counts_does_not_escalate(self):
        escalated = _reconcile_stale_b2c_disbursements(
            self._future_cutoff(),
            max_attempts=3,
        )

        self.assertEqual(escalated, 0)
        self.loan.refresh_from_db()
        self.assertEqual(
            self.loan.disbursement_status,
            Loan.DisbursementStatus.PENDING_CONFIRMATION,
        )
        self.transaction.refresh_from_db()
        self.assertEqual(
            self.transaction.metadata['reconciliation_attempts'],
            1,
        )

    def test_recent_attempt_is_not_touched(self):
        escalated = _reconcile_stale_b2c_disbursements(
            timezone.now() - timedelta(minutes=5),
            max_attempts=1,
        )

        self.assertEqual(escalated, 0)
        self.loan.refresh_from_db()
        self.assertEqual(
            self.loan.disbursement_status,
            Loan.DisbursementStatus.PENDING_CONFIRMATION,
        )

    def test_resolved_by_late_callback_is_skipped(self):
        self.loan.disbursement_status = Loan.DisbursementStatus.DISBURSED
        self.loan.save(update_fields=['disbursement_status', 'updated_at'])

        escalated = _reconcile_stale_b2c_disbursements(
            self._future_cutoff(),
            max_attempts=1,
        )

        self.assertEqual(escalated, 0)
        self.assertFalse(
            DisbursementAuditLog.objects.filter(loan=self.loan).exists()
        )
