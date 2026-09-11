"""End-to-end coverage for SaccoInvoiceFeeCalculator's whole-shilling
policy (see payments/fee_calculator.py's class docstring).

A fractional net amount (e.g. net=550 at a 1% fee -> theoretical gross
555.50) must never reach Daraja, get stored as the callback's expected
amount, or get invoiced, as anything but a whole shilling. This drives
STKPushView for real initiation, then a real STK callback, for both
deposit and repayment - the two transaction types that share
SaccoInvoiceFeeCalculator._calculate_inflow - and checks a genuinely
wrong callback amount still trips AMOUNT_MISMATCH under the new
(rounded) expected value.
"""

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models import Sacco, SaccoPaymentConfig, User
from billing.models import InvoiceLineItem
from payments.fee_calculator import SaccoInvoiceFeeCalculator
from payments.models import Callback, MpesaTransaction, Transaction
from payments.tasks import process_stk_callback_task
from saccomanagement.models import Role
from saccomembership.models import Membership
from services.models import (
    Loan,
    LoanType,
    RepaymentSchedule,
    Saving,
    SavingsType,
)


class _FixtureMixin:
    """A payment-ready SACCO, an approved member, an admin for
    amount-mismatch notifications - the minimum needed for STKPushView to
    accept an initiation (sacco.payment_ready + an active
    SaccoPaymentConfig)."""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            email='rounding-member@example.com',
            phone_number='254712280001',
            password='StrongPass1',
        )
        self.admin = User.objects.create_user(
            email='rounding-admin@example.com',
            phone_number='254712280002',
            password='StrongPass1',
        )
        self.sacco = Sacco.objects.create(
            name='Rounding SACCO',
            registration_number='ROUND-001',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
            membership_type=Sacco.MembershipType.OPEN,
            payment_ready=True,
        )
        SaccoPaymentConfig.objects.create(
            sacco=self.sacco,
            shortcode_type=SaccoPaymentConfig.ShortcodeType.PAYBILL,
            shortcode='600333',
            stk_passkey='round_passkey',
            daraja_consumer_key='round_consumer_key',
            daraja_consumer_secret='round_consumer_secret',
            environment=SaccoPaymentConfig.Environment.SANDBOX,
            is_active=True,
        )
        self.membership = Membership.objects.create(
            user=self.user,
            sacco=self.sacco,
            status=Membership.Status.APPROVED,
            member_number='ROUND-M-001',
        )
        Role.objects.create(
            user=self.admin, sacco=self.sacco, name=Role.SACCO_ADMIN,
        )
        self.client.force_authenticate(user=self.user)

    def _initiate(self, payload, checkout_request_id):
        """POST to STKPushView with DarajaClient.initiate_stk_push mocked
        to assert the amount it receives is already whole, then return
        the created Transaction."""
        with patch(
            'payments.views.DarajaClient.initiate_stk_push',
        ) as stk_mock:
            def fake_stk_push(**kwargs):
                sent_amount = kwargs['amount']
                self.assertEqual(
                    sent_amount, sent_amount.to_integral_value(),
                    'A fractional amount reached Daraja - int() '
                    'truncation in DarajaClient would silently drop it.',
                )
                return {
                    'ResponseCode': '0',
                    'MerchantRequestID': f'MRID-{checkout_request_id}',
                    'CheckoutRequestID': checkout_request_id,
                }

            stk_mock.side_effect = fake_stk_push

            response = self.client.post(
                reverse('payments:mpesa-stk-push'),
                payload,
                format='json',
            )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        return Transaction.objects.get(
            external_reference=checkout_request_id,
        )

    def _deliver_callback(self, payment, checkout_request_id, amount):
        """Simulate a genuine Safaricom STK callback reporting `amount`,
        processed through the real callback task (idempotency record,
        row locking, the lot) - not the raw HTTP endpoint, whose
        IP/signature layer is covered separately in
        test_mpesa_security.py."""
        callback_body = {
            'Body': {
                'stkCallback': {
                    'CheckoutRequestID': checkout_request_id,
                    'ResultCode': 0,
                    'ResultDesc': (
                        'The service request is processed successfully.'
                    ),
                    'CallbackMetadata': {
                        'Item': [
                            {'Name': 'Amount', 'Value': float(amount)},
                            {
                                'Name': 'MpesaReceiptNumber',
                                'Value': f'RCT-{checkout_request_id}',
                            },
                        ],
                    },
                },
            },
        }
        callback = Callback.objects.create(
            transaction=payment,
            provider=payment.provider,
            raw_payload=callback_body,
        )
        process_stk_callback_task(str(callback.id))
        payment.refresh_from_db()
        return payment


class DepositWholeShillingEndToEndTest(_FixtureMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.savings_type = SavingsType.objects.create(
            sacco=self.sacco,
            name=SavingsType.Name.BOSA,
            minimum_contribution=Decimal('100.00'),
        )
        self.saving = Saving.objects.create(
            membership=self.membership,
            savings_type=self.savings_type,
            amount=Decimal('0.00'),
            total_contributions=Decimal('0.00'),
            status=Saving.Status.ACTIVE,
        )

    def _run(self, net_amount, checkout_request_id):
        breakdown = SaccoInvoiceFeeCalculator().calculate(
            'deposit', Decimal(net_amount),
        )
        payment = self._initiate(
            {
                'phone_number': '254712280001',
                'amount': net_amount,
                'purpose': 'SAVING_DEPOSIT',
                'sacco_id': str(self.sacco.id),
                'saving_id': str(self.saving.id),
            },
            checkout_request_id,
        )
        self.assertEqual(payment.gross_amount, breakdown['gross_amount'])

        payment = self._deliver_callback(
            payment, checkout_request_id, breakdown['gross_amount'],
        )
        return payment, breakdown

    def test_fractional_net_amounts_complete_with_correct_fee(self):
        cases = ['550.00', '1250.00', '333.00', '100.00']
        running_balance = Decimal('0.00')

        for index, net_amount in enumerate(cases):
            checkout_request_id = f'CRID-ROUND-DEP-{index}'
            with self.subTest(net_amount=net_amount):
                payment, breakdown = self._run(
                    net_amount, checkout_request_id,
                )

                self.assertEqual(
                    payment.status, Transaction.Status.COMPLETED,
                )
                self.assertEqual(payment.amount, Decimal(net_amount))

                running_balance += Decimal(net_amount)
                self.saving.refresh_from_db()
                self.assertEqual(self.saving.amount, running_balance)

                # The fee actually invoiced is rounded_gross - net, not
                # the theoretical percentage figure.
                line_item = InvoiceLineItem.objects.get(transaction=payment)
                self.assertEqual(
                    line_item.platform_fee, breakdown['platform_fee'],
                )
                self.assertEqual(
                    line_item.gross_amount, breakdown['gross_amount'],
                )

    def test_wrong_callback_amount_still_trips_amount_mismatch(self):
        breakdown = SaccoInvoiceFeeCalculator().calculate(
            'deposit', Decimal('550.00'),
        )
        payment = self._initiate(
            {
                'phone_number': '254712280001',
                'amount': '550.00',
                'purpose': 'SAVING_DEPOSIT',
                'sacco_id': str(self.sacco.id),
                'saving_id': str(self.saving.id),
            },
            'CRID-ROUND-DEP-MISMATCH',
        )
        self.assertEqual(payment.gross_amount, breakdown['gross_amount'])

        # A genuinely wrong amount - not a rounding artifact.
        payment = self._deliver_callback(
            payment, 'CRID-ROUND-DEP-MISMATCH', Decimal('500.00'),
        )

        self.assertEqual(
            payment.status, Transaction.Status.AMOUNT_MISMATCH,
        )
        self.saving.refresh_from_db()
        self.assertEqual(self.saving.amount, Decimal('0.00'))
        self.assertFalse(
            InvoiceLineItem.objects.filter(transaction=payment).exists()
        )


class RepaymentWholeShillingEndToEndTest(_FixtureMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.loan_type = LoanType.objects.create(
            sacco=self.sacco,
            name='Rounding Loan',
            interest_rate=Decimal('12.00'),
            max_term_months=12,
            min_amount=Decimal('100.00'),
        )
        self.loan = Loan.objects.create(
            membership=self.membership,
            loan_type=self.loan_type,
            amount=Decimal('5000.00'),
            interest_rate=Decimal('12.00'),
            term_months=3,
            outstanding_balance=Decimal('5000.00'),
            status=Loan.Status.ACTIVE,
        )
        # One large instalment absorbs several partial repayments across
        # subTest iterations without ever going fully PAID.
        RepaymentSchedule.objects.create(
            loan=self.loan,
            instalment_number=1,
            due_date=timezone.localdate() + timedelta(days=30),
            amount=Decimal('5000.00'),
            principal=Decimal('4500.00'),
            interest=Decimal('500.00'),
            balance_after=Decimal('0.00'),
        )

    def _run(self, net_amount, checkout_request_id):
        breakdown = SaccoInvoiceFeeCalculator().calculate(
            'repayment', Decimal(net_amount),
        )
        payment = self._initiate(
            {
                'phone_number': '254712280001',
                'amount': net_amount,
                'purpose': 'LOAN_REPAYMENT',
                'sacco_id': str(self.sacco.id),
                'loan_id': str(self.loan.id),
                'instalment_number': 1,
            },
            checkout_request_id,
        )
        self.assertEqual(payment.gross_amount, breakdown['gross_amount'])

        payment = self._deliver_callback(
            payment, checkout_request_id, breakdown['gross_amount'],
        )
        return payment, breakdown

    def test_fractional_net_amounts_complete_with_correct_fee(self):
        cases = ['550.00', '1250.00', '333.00', '100.00']
        running_outstanding = Decimal('5000.00')

        for index, net_amount in enumerate(cases):
            checkout_request_id = f'CRID-ROUND-REP-{index}'
            with self.subTest(net_amount=net_amount):
                payment, breakdown = self._run(
                    net_amount, checkout_request_id,
                )

                self.assertEqual(
                    payment.status, Transaction.Status.COMPLETED,
                )
                self.assertEqual(payment.amount, Decimal(net_amount))

                running_outstanding -= Decimal(net_amount)
                self.loan.refresh_from_db()
                self.assertEqual(
                    self.loan.outstanding_balance, running_outstanding,
                )

                line_item = InvoiceLineItem.objects.get(transaction=payment)
                self.assertEqual(
                    line_item.platform_fee, breakdown['platform_fee'],
                )
                self.assertEqual(
                    line_item.gross_amount, breakdown['gross_amount'],
                )

    def test_wrong_callback_amount_still_trips_amount_mismatch(self):
        breakdown = SaccoInvoiceFeeCalculator().calculate(
            'repayment', Decimal('550.00'),
        )
        payment = self._initiate(
            {
                'phone_number': '254712280001',
                'amount': '550.00',
                'purpose': 'LOAN_REPAYMENT',
                'sacco_id': str(self.sacco.id),
                'loan_id': str(self.loan.id),
                'instalment_number': 1,
            },
            'CRID-ROUND-REP-MISMATCH',
        )
        self.assertEqual(payment.gross_amount, breakdown['gross_amount'])

        payment = self._deliver_callback(
            payment, 'CRID-ROUND-REP-MISMATCH', Decimal('500.00'),
        )

        self.assertEqual(
            payment.status, Transaction.Status.AMOUNT_MISMATCH,
        )
        self.loan.refresh_from_db()
        self.assertEqual(self.loan.outstanding_balance, Decimal('5000.00'))
