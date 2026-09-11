"""Ops visibility for AMOUNT_MISMATCH and INITIATION_FAILED.

Both used to only produce an info/warning log line - no counter for
volume/trend, and no path into whatever gets a human's attention.

This project already has both pieces, so this extends them rather than
adding a new observability dependency:
  - config.utils.emit_metric - the project's structured counter-increment
    log line (grep shows it already used by payments.withdrawals,
    accounts.views, services.tasks, saccomanagement.odpc_logging).
  - sentry_sdk (a real requirements.txt dependency, already used by
    services.tasks' B2C disbursement escalation and accounts.otp_backends)
    for "someone should look at this" alerts.

Design note the tests below pin down: an ambiguous Daraja timeout is not
a failure by this project's own established convention (see
payments.disbursements._mark_b2c_attempt_failed's docstring) - so the
counter fires for a timeout too (trend visibility is still useful), but
the Sentry alert does not; only a genuine, unambiguous failure pages.
AMOUNT_MISMATCH has no such ambiguous case, so it always alerts.
"""

import sys
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.test import TestCase

from accounts.models import Sacco, User
from payments.disbursements import _mark_b2c_attempt_failed
from payments.integrations.mpesa.daraja import DarajaError
from payments.models import MpesaTransaction, PaymentProvider, Transaction
from payments.tasks import _handle_amount_mismatch
from payments.views import STKPushView
from saccomembership.models import Membership
from services.models import Loan, LoanType


def _push_fake_sentry():
    """Install a MagicMock as sys.modules['sentry_sdk'] so the lazy
    ``import sentry_sdk`` inside each alert site picks it up, mirroring
    services/tests/test_disbursement_tasks.py's own convention. Returns
    the mock; caller must restore via _pop_fake_sentry in a finally."""
    mock_sentry = MagicMock()
    sys.modules['sentry_sdk'] = mock_sentry
    return mock_sentry


def _pop_fake_sentry():
    sys.modules.pop('sentry_sdk', None)


class OpsAlertingFixtureMixin:
    def _make_sacco_and_provider(self, reg):
        sacco = Sacco.objects.create(
            name=f'Ops Alert {reg}',
            registration_number=reg,
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
        )
        provider, _ = PaymentProvider.objects.get_or_create(
            name='M-Pesa',
            defaults={
                'provider_type': PaymentProvider.ProviderType.MPESA,
                'is_active': True,
            },
        )
        return sacco, provider


class AmountMismatchAlertingTest(OpsAlertingFixtureMixin, TestCase):
    """payments.tasks._handle_amount_mismatch."""

    def setUp(self):
        self.sacco, self.provider = self._make_sacco_and_provider(
            'OPSALERT01',
        )
        self.user = User.objects.create_user(
            email='ops-alert-member@example.com',
            phone_number='254712299001',
            password='StrongPass1',
        )
        self.transaction = Transaction.objects.create(
            provider=self.provider,
            user=self.user,
            reference='SS-OPSALERT-01',
            transaction_type=Transaction.TransactionType.DEPOSIT,
            amount=Decimal('100.00'),
            gross_amount=Decimal('100.00'),
            sacco=self.sacco,
            status=Transaction.Status.PENDING,
        )
        self.mpesa_transaction = MpesaTransaction.objects.create(
            transaction=self.transaction,
            phone_number='254712299001',
        )

    @patch('config.utils.emit_metric')
    def test_amount_mismatch_emits_metric_with_sacco_and_type_labels(
        self, mock_emit_metric,
    ):
        _handle_amount_mismatch(
            self.mpesa_transaction,
            self.transaction,
            'RECEIPT001',
            Decimal('90.00'),
            Decimal('100.00'),
            Decimal('10.00'),
        )

        mock_emit_metric.assert_called_once_with(
            'mpesa_amount_mismatch',
            sacco_id=str(self.sacco.id),
            transaction_type=Transaction.TransactionType.DEPOSIT,
            mpesa_transaction_type=MpesaTransaction.TransactionType.STK_PUSH,
        )

    def test_amount_mismatch_sends_sentry_alert(self):
        mock_sentry = _push_fake_sentry()
        try:
            _handle_amount_mismatch(
                self.mpesa_transaction,
                self.transaction,
                'RECEIPT001',
                Decimal('90.00'),
                Decimal('100.00'),
                Decimal('10.00'),
            )

            mock_sentry.set_context.assert_called_once()
            context_name, context_data = mock_sentry.set_context.call_args[0]
            self.assertEqual(context_name, 'mpesa_amount_mismatch')
            self.assertEqual(
                context_data['sacco_id'], str(self.sacco.id),
            )

            mock_sentry.capture_message.assert_called_once()
            message, kwargs = mock_sentry.capture_message.call_args
            self.assertIn(str(self.transaction.id), message[0])
            self.assertEqual(kwargs['level'], 'error')
        finally:
            _pop_fake_sentry()

    @patch('config.utils.emit_metric', side_effect=RuntimeError('boom'))
    def test_a_metrics_failure_does_not_break_the_mismatch_handling(
        self, _mock_emit_metric,
    ):
        # A metrics/alerting hiccup must never affect the money-adjacent
        # status write that already happened.
        _handle_amount_mismatch(
            self.mpesa_transaction,
            self.transaction,
            'RECEIPT001',
            Decimal('90.00'),
            Decimal('100.00'),
            Decimal('10.00'),
        )

        self.transaction.refresh_from_db()
        self.assertEqual(
            self.transaction.status, Transaction.Status.AMOUNT_MISMATCH,
        )


class STKInitiationFailedAlertingTest(OpsAlertingFixtureMixin, TestCase):
    """payments.views.STKPushView._mark_stk_initiation_failed, via the
    module-level payments.views._alert_stk_initiation_failed it calls."""

    def setUp(self):
        self.sacco, self.provider = self._make_sacco_and_provider(
            'OPSALERT02',
        )
        self.user = User.objects.create_user(
            email='ops-alert-member2@example.com',
            phone_number='254712299011',
            password='StrongPass1',
        )
        self.transaction = Transaction.objects.create(
            provider=self.provider,
            user=self.user,
            reference='SS-OPSALERT-02',
            transaction_type=Transaction.TransactionType.DEPOSIT,
            amount=Decimal('100.00'),
            gross_amount=Decimal('100.00'),
            sacco=self.sacco,
            status=Transaction.Status.PENDING,
        )
        self.mpesa_transaction = MpesaTransaction.objects.create(
            transaction=self.transaction,
            phone_number='254712299011',
        )
        self.view = STKPushView()

    @patch('config.utils.emit_metric')
    def test_genuine_failure_emits_metric_with_labels(
        self, mock_emit_metric,
    ):
        exc = DarajaError('Invalid credentials.', response_code='401.002')

        self.view._mark_stk_initiation_failed(
            self.transaction, self.mpesa_transaction, exc,
        )

        mock_emit_metric.assert_called_once_with(
            'mpesa_stk_initiation_failed',
            sacco_id=str(self.sacco.id),
            transaction_type=Transaction.TransactionType.DEPOSIT,
            mpesa_transaction_type=MpesaTransaction.TransactionType.STK_PUSH,
            status_unknown='false',
        )

    def test_genuine_failure_sends_sentry_alert(self):
        exc = DarajaError('Invalid credentials.', response_code='401.002')
        mock_sentry = _push_fake_sentry()
        try:
            self.view._mark_stk_initiation_failed(
                self.transaction, self.mpesa_transaction, exc,
            )

            mock_sentry.capture_message.assert_called_once()
            message, kwargs = mock_sentry.capture_message.call_args
            self.assertIn(str(self.transaction.id), message[0])
            self.assertEqual(kwargs['level'], 'error')
        finally:
            _pop_fake_sentry()

    @patch('config.utils.emit_metric')
    def test_ambiguous_timeout_emits_metric_but_does_not_alert(
        self, mock_emit_metric,
    ):
        exc = DarajaError(
            'The request timed out.', response_code=None, is_timeout=True,
        )
        mock_sentry = _push_fake_sentry()
        try:
            self.view._mark_stk_initiation_failed(
                self.transaction,
                self.mpesa_transaction,
                exc,
                status_unknown=True,
            )

            # Trend visibility still fires for a timeout.
            mock_emit_metric.assert_called_once_with(
                'mpesa_stk_initiation_failed',
                sacco_id=str(self.sacco.id),
                transaction_type=Transaction.TransactionType.DEPOSIT,
                mpesa_transaction_type=(
                    MpesaTransaction.TransactionType.STK_PUSH
                ),
                status_unknown='true',
            )
            # But an ambiguous, self-healing timeout does not page anyone.
            mock_sentry.capture_message.assert_not_called()
        finally:
            _pop_fake_sentry()


class B2CInitiationTimeoutAlertingTest(OpsAlertingFixtureMixin, TestCase):
    """payments.disbursements._mark_b2c_attempt_failed - the B2C
    equivalent of an ambiguous initiation timeout: same INITIATION_FAILED
    status, same "counter yes, page no" treatment."""

    def setUp(self):
        self.sacco, self.provider = self._make_sacco_and_provider(
            'OPSALERT03',
        )
        self.user = User.objects.create_user(
            email='ops-alert-member3@example.com',
            phone_number='254712299021',
            password='StrongPass1',
        )
        membership = Membership.objects.create(
            user=self.user,
            sacco=self.sacco,
            status=Membership.Status.APPROVED,
            member_number='OPSALERT03-M-001',
        )
        loan_type = LoanType.objects.create(
            sacco=self.sacco,
            name='Ops Alert Loan',
            interest_rate=Decimal('12.00'),
            max_term_months=12,
            min_amount=Decimal('100.00'),
            requires_guarantors=False,
        )
        self.loan = Loan.objects.create(
            membership=membership,
            loan_type=loan_type,
            amount=Decimal('500.00'),
            interest_rate=Decimal('12.00'),
            term_months=6,
            outstanding_balance=Decimal('0.00'),
            status=Loan.Status.APPROVED,
            disbursement_status=Loan.DisbursementStatus.INITIATED,
        )
        self.transaction = Transaction.objects.create(
            provider=self.provider,
            user=self.user,
            reference='SS-OPSALERT-03',
            transaction_type=Transaction.TransactionType.LOAN_DISBURSEMENT,
            amount=Decimal('500.00'),
            gross_amount=Decimal('500.00'),
            sacco=self.sacco,
            status=Transaction.Status.PENDING,
        )
        self.mpesa_transaction = MpesaTransaction.objects.create(
            transaction=self.transaction,
            phone_number='254712299021',
            transaction_type=MpesaTransaction.TransactionType.B2C,
            related_loan=self.loan,
        )

    @patch('config.utils.emit_metric')
    def test_timeout_emits_metric_with_labels(self, mock_emit_metric):
        exc = DarajaError(
            'The request timed out.', response_code=None, is_timeout=True,
        )

        _mark_b2c_attempt_failed(
            self.transaction,
            self.mpesa_transaction,
            self.loan,
            exc,
            is_timeout=True,
        )

        self.transaction.refresh_from_db()
        self.assertEqual(
            self.transaction.status, Transaction.Status.INITIATION_FAILED,
        )
        mock_emit_metric.assert_called_once_with(
            'mpesa_b2c_initiation_failed',
            sacco_id=str(self.sacco.id),
            transaction_type=Transaction.TransactionType.LOAN_DISBURSEMENT,
            mpesa_transaction_type=MpesaTransaction.TransactionType.B2C,
            status_unknown='true',
        )

    @patch('config.utils.emit_metric')
    def test_hard_failure_does_not_emit_this_metric(self, mock_emit_metric):
        # is_timeout=False sets Transaction.Status.FAILED, not
        # INITIATION_FAILED - out of scope for this metric, which tracks
        # the INITIATION_FAILED transition specifically.
        exc = DarajaError('Invalid initiator credentials.', '2001')

        _mark_b2c_attempt_failed(
            self.transaction,
            self.mpesa_transaction,
            self.loan,
            exc,
            is_timeout=False,
        )

        self.transaction.refresh_from_db()
        self.assertEqual(
            self.transaction.status, Transaction.Status.FAILED,
        )
        mock_emit_metric.assert_not_called()
