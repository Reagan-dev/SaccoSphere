"""Tests for the automated compliance-flag detector framework."""

from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase

from accounts.models import Sacco, User
from payments.models import MpesaTransaction, Transaction
from saccomanagement.compliance_detectors import (
    PAYMENT_FAILURE_CONSECUTIVE_THRESHOLD,
    RepeatedPaymentFailureDetector,
)
from saccomanagement.models import ComplianceFlag


class RepeatedPaymentFailureDetectorTestCase(TestCase):
    def setUp(self):
        self.sacco = Sacco.objects.create(
            name='Detector SACCO',
            registration_number='DET001',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
        )
        self.other_sacco = Sacco.objects.create(
            name='Other Detector SACCO',
            registration_number='DET002',
            sector=Sacco.Sector.FINANCE,
            county='Kisumu',
        )
        self.user = User.objects.create_user(
            email='detector-payer@example.com',
            password='secret',
            phone_number='254712345600',
        )
        self._reference_counter = 0

    def _transaction(self, sacco, status_value):
        self._reference_counter += 1
        return Transaction.objects.create(
            user=self.user,
            sacco=sacco,
            reference=f'DET-REF-{self._reference_counter}',
            transaction_type=Transaction.TransactionType.DEPOSIT,
            amount=Decimal('100.00'),
            status=status_value,
        )

    def _fail(self, sacco):
        transaction = self._transaction(sacco, Transaction.Status.FAILED)
        return transaction, RepeatedPaymentFailureDetector().check(transaction)

    def test_creates_flag_once_threshold_crossed(self):
        for _ in range(PAYMENT_FAILURE_CONSECUTIVE_THRESHOLD - 1):
            _, flag = self._fail(self.sacco)
            self.assertIsNone(flag)

        _, flag = self._fail(self.sacco)

        self.assertIsNotNone(flag)
        self.assertEqual(flag.sacco, self.sacco)
        self.assertEqual(flag.flag_type, ComplianceFlag.FlagType.PAYMENT_FAILURE)
        self.assertEqual(flag.status, ComplianceFlag.Status.OPEN)
        self.assertEqual(
            ComplianceFlag.objects.filter(sacco=self.sacco).count(), 1,
        )

    def test_below_threshold_does_not_create_a_flag(self):
        for _ in range(PAYMENT_FAILURE_CONSECUTIVE_THRESHOLD - 1):
            self._fail(self.sacco)

        self.assertFalse(
            ComplianceFlag.objects.filter(sacco=self.sacco).exists(),
        )

    def test_a_success_breaking_the_streak_prevents_flagging(self):
        self._fail(self.sacco)
        self._transaction(self.sacco, Transaction.Status.COMPLETED)
        for _ in range(PAYMENT_FAILURE_CONSECUTIVE_THRESHOLD - 1):
            self._fail(self.sacco)

        self.assertFalse(
            ComplianceFlag.objects.filter(sacco=self.sacco).exists(),
        )

    def test_subsequent_failure_updates_existing_flag_not_duplicate(self):
        for _ in range(PAYMENT_FAILURE_CONSECUTIVE_THRESHOLD):
            self._fail(self.sacco)

        self._fail(self.sacco)

        self.assertEqual(
            ComplianceFlag.objects.filter(sacco=self.sacco).count(), 1,
        )
        flag = ComplianceFlag.objects.get(sacco=self.sacco)
        self.assertEqual(flag.metadata['occurrence_count'], 2)

    def test_different_sacco_failures_do_not_affect_first_saccos_flag(self):
        for _ in range(PAYMENT_FAILURE_CONSECUTIVE_THRESHOLD):
            self._fail(self.sacco)
        flag = ComplianceFlag.objects.get(sacco=self.sacco)

        for _ in range(PAYMENT_FAILURE_CONSECUTIVE_THRESHOLD):
            self._fail(self.other_sacco)

        flag.refresh_from_db()
        self.assertEqual(flag.metadata['occurrence_count'], 1)
        self.assertTrue(
            ComplianceFlag.objects.filter(sacco=self.other_sacco).exists(),
        )
        self.assertEqual(ComplianceFlag.objects.count(), 2)

    def test_transaction_with_no_sacco_is_skipped(self):
        transaction = Transaction.objects.create(
            user=self.user,
            sacco=None,
            reference='DET-REF-NO-SACCO',
            transaction_type=Transaction.TransactionType.DEPOSIT,
            amount=Decimal('100.00'),
            status=Transaction.Status.FAILED,
        )

        flag = RepeatedPaymentFailureDetector().check(transaction)

        self.assertIsNone(flag)


class PaymentCallbackDetectorWiringTestCase(TestCase):
    """
    Confirms the detector is actually wired into the real M-Pesa STK
    callback failure path (payments.tasks._process_failed_callback), not
    just callable on its own.
    """

    def setUp(self):
        self.sacco = Sacco.objects.create(
            name='Wiring SACCO',
            registration_number='WIRE001',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
        )
        self.user = User.objects.create_user(
            email='wiring-payer@example.com',
            password='secret',
            phone_number='254712345700',
        )
        self._reference_counter = 0

    def _failed_callback(self):
        from payments.tasks import _process_failed_callback

        self._reference_counter += 1
        transaction = Transaction.objects.create(
            user=self.user,
            sacco=self.sacco,
            reference=f'WIRE-REF-{self._reference_counter}',
            transaction_type=Transaction.TransactionType.DEPOSIT,
            amount=Decimal('100.00'),
            status=Transaction.Status.PENDING,
        )
        mpesa_transaction = MpesaTransaction.objects.create(
            transaction=transaction,
            phone_number=self.user.phone_number,
            checkout_request_id=f'CHECKOUT-{self._reference_counter}',
        )
        # _process_failed_callback also dispatches a member notification
        # via Celery .delay() - unrelated to what this test is about, and
        # this environment has no broker to actually reach, so mock it out
        # rather than let the test block on real (and doomed) network
        # retries.
        with patch('payments.tasks._notify_payment_failure'):
            _process_failed_callback(
                mpesa_transaction,
                transaction,
                {'ResultDesc': 'Insufficient funds.'},
                1,
            )
        return transaction

    def test_real_failed_callback_path_creates_flag_at_threshold(self):
        for _ in range(PAYMENT_FAILURE_CONSECUTIVE_THRESHOLD):
            self._failed_callback()

        self.assertTrue(
            ComplianceFlag.objects.filter(
                sacco=self.sacco,
                flag_type=ComplianceFlag.FlagType.PAYMENT_FAILURE,
            ).exists(),
        )
