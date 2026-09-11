"""B2C withdrawal reconciliation: closing out SENT/PENDING_CONFIRMATION
withdrawals whose result callback never arrives.

payments.tasks._reconcile_stale_b2c_withdrawals is the withdrawal-side
sibling of _reconcile_stale_b2c_disbursements, and shares its central
constraint: B2C has no synchronous Daraja transaction-status query to call
(Safaricom's real TransactionStatusQuery API only acknowledges the
request; the actual outcome arrives later at a ResultURL callback), and a
PENDING_CONFIRMATION withdrawal - whose initiate call itself timed out -
has no conversation_id to query with at all. So reconciliation never
resolves the money outcome itself; it only counts attempts and, past the
ceiling, escalates to UNDER_REVIEW (an admin-queue marker, never a lock,
never a balance/ledger change). The only thing that can ever resolve the
transaction is a genuine result callback - early or late - landing on the
existing idempotent processors.

Covers:
  * below max attempts -> counted, not escalated, status unchanged
    ("still pending" outcome);
  * a callback landing before escalation resolves normally, success or
    failure ("Daraja confirms success" / "confirms failure" outcomes,
    delivered the only way B2C ever really delivers them - a callback);
  * past max attempts with still no callback -> UNDER_REVIEW, audited
    with sacco_id, balance/ledger untouched;
  * a genuinely late callback after escalation still resolves cleanly and
    is logged as ordinary "already processed" handling, never flagged as
    a replay attack;
  * cross-connection race between reconciliation's escalation attempt and
    a genuine callback delivery on the same transaction - mirrors
    B2CCallbackConcurrencyRegressionTests;
  * a stuck first delivery with no redelivery ever arriving still
    recovers via escalation - one of the two paths the replay-detection
    state-awareness regression test covers (the other, STK redelivery
    being accepted, lives in test_replay_recovery_regression).
"""

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal

from django.db import connection
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from accounts.models import Sacco, SaccoPaymentConfig, User
from ledger.models import LedgerEntry
from ledger.utils import apply_ledger_entry
from payments.models import (
    Callback,
    MpesaTransaction,
    PaymentProvider,
    Transaction,
)
from payments.tasks import (
    _reconcile_stale_b2c_withdrawals,
    process_b2c_callback_task,
)
from saccomanagement.models import SystemAuditLog
from saccomembership.models import Membership
from services.models import Saving, SavingsType


def _make_withdrawal_ready_sacco(reg, email, phone):
    sacco = Sacco.objects.create(
        name=f'WD Recon {reg}',
        registration_number=reg,
        sector=Sacco.Sector.FINANCE,
        county='Nairobi',
        membership_type=Sacco.MembershipType.OPEN,
        payment_ready=True,
    )
    SaccoPaymentConfig.objects.create(
        sacco=sacco,
        shortcode_type=SaccoPaymentConfig.ShortcodeType.PAYBILL,
        shortcode='600222',
        stk_passkey='wdr_passkey',
        daraja_consumer_key='wdr_consumer_key',
        daraja_consumer_secret='wdr_consumer_secret',
        environment=SaccoPaymentConfig.Environment.SANDBOX,
        b2c_initiator_name='wdr_initiator',
        b2c_security_credential='wdr_security_credential',
        is_active=True,
    )
    user = User.objects.create_user(
        email=email,
        phone_number=phone,
        password='StrongPass1',
    )
    membership = Membership.objects.create(
        user=user,
        sacco=sacco,
        status=Membership.Status.APPROVED,
        member_number=f'{reg}-M-001',
    )
    savings_type = SavingsType.objects.create(
        sacco=sacco,
        name=SavingsType.Name.BOSA,
        minimum_contribution=Decimal('100.00'),
    )
    saving = Saving.objects.create(
        membership=membership,
        savings_type=savings_type,
        amount=Decimal('10000.00'),
        total_contributions=Decimal('10000.00'),
        status=Saving.Status.ACTIVE,
    )
    provider, _ = PaymentProvider.objects.get_or_create(
        name='M-Pesa',
        defaults={
            'provider_type': PaymentProvider.ProviderType.MPESA,
            'is_active': True,
        },
    )
    return sacco, user, membership, saving, provider


def _reserve_withdrawal_fixture(
    *,
    membership,
    saving,
    provider,
    reference,
    conversation_id,
    gross=Decimal('5000.00'),
    net=Decimal('4975.00'),
    status=Transaction.Status.SENT,
):
    """Build a Transaction + MpesaTransaction as if initiate_savings_
    withdrawal had already reserved the balance and (for SENT) received
    Daraja's synchronous accept. Mirrors B2CReconciliationTests' direct-
    fixture style (payments.tests.test_payments) rather than driving the
    full initiation view - tests pass a cutoff in the future to treat a
    freshly-created row as stale, exactly as that class does.
    """
    has_conversation_id = status == Transaction.Status.SENT
    payment = Transaction.objects.create(
        provider=provider,
        sacco=membership.sacco,
        user=membership.user,
        reference=reference,
        transaction_type=Transaction.TransactionType.WITHDRAWAL,
        amount=net,
        gross_amount=gross,
        platform_fee=gross - net,
        status=status,
        external_reference=conversation_id if has_conversation_id else None,
        description=f'Savings withdrawal - {saving.id}',
    )
    mpesa_transaction = MpesaTransaction.objects.create(
        transaction=payment,
        phone_number=membership.user.phone_number,
        transaction_type=MpesaTransaction.TransactionType.B2C,
        related_saving=saving,
        conversation_id=conversation_id if has_conversation_id else None,
        originator_conversation_id=(
            f'ORIG-{conversation_id}' if has_conversation_id else None
        ),
    )
    # The balance was already debited at initiation - reproduce that with
    # the same ledger-writing path the real initiation code uses.
    apply_ledger_entry(
        saving=saving,
        amount=gross,
        entry_type=LedgerEntry.EntryType.DEBIT,
        category=LedgerEntry.Category.SAVING_WITHDRAWAL,
        description='Reconciliation test fixture debit.',
        reference=str(payment.id),
        transaction=payment,
        withdrawal_delta=gross,
    )
    return payment, mpesa_transaction


def _b2c_result_ok(conversation_id, receipt='RCPT-RECON-OK'):
    return {
        'ResultType': 0,
        'ResultCode': 0,
        'ResultDesc': 'The service request is processed successfully.',
        'OriginatorConversationID': f'ORIG-{conversation_id}',
        'ConversationID': conversation_id,
        'TransactionID': f'TXN-{conversation_id}',
        'ResultParameters': {
            'ResultParameter': [
                {'Key': 'TransactionReceipt', 'Value': receipt},
                {'Key': 'TransactionAmount', 'Value': 4975},
            ],
        },
    }


def _b2c_result_failed(conversation_id, code=2001):
    return {
        'ResultType': 0,
        'ResultCode': code,
        'ResultDesc': 'The initiator information is invalid.',
        'OriginatorConversationID': f'ORIG-{conversation_id}',
        'ConversationID': conversation_id,
        'TransactionID': '',
    }


class WithdrawalReconciliationAttemptTest(TestCase):
    """Attempt-counting and escalation, mirroring B2CReconciliationTests."""

    def setUp(self):
        (
            self.sacco,
            self.user,
            self.membership,
            self.saving,
            self.provider,
        ) = _make_withdrawal_ready_sacco(
            'WDR01', 'wdr-attempt@example.com', '254712270001',
        )

    def _future_cutoff(self):
        return timezone.now() + timedelta(minutes=1)

    def test_below_max_attempts_only_counts_does_not_escalate(self):
        payment, mpesa = _reserve_withdrawal_fixture(
            membership=self.membership,
            saving=self.saving,
            provider=self.provider,
            reference='WDR-RECON-001',
            conversation_id='CONV-WDR-001',
        )

        escalated = _reconcile_stale_b2c_withdrawals(
            self._future_cutoff(), max_attempts=3,
        )

        self.assertEqual(escalated, 0)
        payment.refresh_from_db()
        self.assertEqual(payment.status, Transaction.Status.SENT)
        self.assertEqual(payment.metadata['reconciliation_attempts'], 1)

        # Balance stays debited - reconciliation never reverses.
        self.saving.refresh_from_db()
        self.assertEqual(self.saving.amount, Decimal('5000.00'))

    def test_recent_attempt_is_not_touched(self):
        payment, mpesa = _reserve_withdrawal_fixture(
            membership=self.membership,
            saving=self.saving,
            provider=self.provider,
            reference='WDR-RECON-002',
            conversation_id='CONV-WDR-002',
        )

        escalated = _reconcile_stale_b2c_withdrawals(
            timezone.now() - timedelta(minutes=5), max_attempts=1,
        )

        self.assertEqual(escalated, 0)
        payment.refresh_from_db()
        self.assertNotIn('reconciliation_attempts', payment.metadata)

    def test_pending_confirmation_status_is_also_picked_up(self):
        payment, mpesa = _reserve_withdrawal_fixture(
            membership=self.membership,
            saving=self.saving,
            provider=self.provider,
            reference='WDR-RECON-003',
            conversation_id='CONV-WDR-003',
            status=Transaction.Status.PENDING_CONFIRMATION,
        )

        escalated = _reconcile_stale_b2c_withdrawals(
            self._future_cutoff(), max_attempts=1,
        )

        self.assertEqual(escalated, 1)
        payment.refresh_from_db()
        self.assertEqual(payment.status, Transaction.Status.UNDER_REVIEW)

    def test_escalates_to_under_review_after_max_attempts(self):
        payment, mpesa = _reserve_withdrawal_fixture(
            membership=self.membership,
            saving=self.saving,
            provider=self.provider,
            reference='WDR-RECON-004',
            conversation_id='CONV-WDR-004',
        )

        escalated = _reconcile_stale_b2c_withdrawals(
            self._future_cutoff(), max_attempts=1,
        )

        self.assertEqual(escalated, 1)
        payment.refresh_from_db()
        self.assertEqual(payment.status, Transaction.Status.UNDER_REVIEW)

        # Never touches money.
        self.assertFalse(
            LedgerEntry.objects.filter(
                reference=f'{payment.id}-REV',
            ).exists()
        )
        self.saving.refresh_from_db()
        self.assertEqual(self.saving.amount, Decimal('5000.00'))

        row = SystemAuditLog.objects.get(
            action='SAVINGS_WITHDRAWAL_UNDER_REVIEW',
            resource_type='Saving',
            resource_id=str(self.saving.id),
        )
        self.assertEqual(row.new_values['sacco_id'], str(self.sacco.id))
        self.assertEqual(row.new_values['transaction_id'], str(payment.id))
        self.assertEqual(row.new_values['reconciliation_attempts'], 1)

    def test_resolved_by_late_callback_is_skipped(self):
        payment, mpesa = _reserve_withdrawal_fixture(
            membership=self.membership,
            saving=self.saving,
            provider=self.provider,
            reference='WDR-RECON-005',
            conversation_id='CONV-WDR-005',
        )
        payment.status = Transaction.Status.COMPLETED
        payment.save(update_fields=['status'])

        escalated = _reconcile_stale_b2c_withdrawals(
            self._future_cutoff(), max_attempts=1,
        )

        self.assertEqual(escalated, 0)
        payment.refresh_from_db()
        self.assertEqual(payment.status, Transaction.Status.COMPLETED)
        self.assertNotIn('reconciliation_attempts', payment.metadata)
        self.assertFalse(
            SystemAuditLog.objects.filter(
                action='SAVINGS_WITHDRAWAL_UNDER_REVIEW',
            ).exists()
        )


class WithdrawalReconciliationCallbackResolutionTest(TestCase):
    """Real resolution only ever comes from a genuine result callback."""

    def setUp(self):
        (
            self.sacco,
            self.user,
            self.membership,
            self.saving,
            self.provider,
        ) = _make_withdrawal_ready_sacco(
            'WDR02', 'wdr-callback@example.com', '254712270002',
        )

    def _deliver(self, payment, *, ok, conversation_id):
        callback_body = {
            'Result': (
                _b2c_result_ok(conversation_id)
                if ok else _b2c_result_failed(conversation_id)
            ),
        }
        callback = Callback.objects.create(
            transaction=payment,
            provider=self.provider,
            raw_payload=callback_body,
        )
        with self.captureOnCommitCallbacks(execute=True):
            process_b2c_callback_task(str(callback.id))
        return callback

    def test_daraja_confirms_success_before_escalation(self):
        payment, mpesa = _reserve_withdrawal_fixture(
            membership=self.membership,
            saving=self.saving,
            provider=self.provider,
            reference='WDR-CB-001',
            conversation_id='CONV-CB-001',
        )
        escalated = _reconcile_stale_b2c_withdrawals(
            timezone.now() + timedelta(minutes=1), max_attempts=5,
        )
        self.assertEqual(escalated, 0)

        self._deliver(payment, ok=True, conversation_id='CONV-CB-001')

        payment.refresh_from_db()
        self.assertEqual(payment.status, Transaction.Status.COMPLETED)
        self.assertEqual(
            LedgerEntry.objects.filter(
                category=LedgerEntry.Category.SAVING_WITHDRAWAL,
                membership=self.membership,
            ).count(),
            1,
        )
        self.saving.refresh_from_db()
        self.assertEqual(self.saving.amount, Decimal('5000.00'))

    def test_daraja_confirms_failure_before_escalation(self):
        payment, mpesa = _reserve_withdrawal_fixture(
            membership=self.membership,
            saving=self.saving,
            provider=self.provider,
            reference='WDR-CB-002',
            conversation_id='CONV-CB-002',
        )
        escalated = _reconcile_stale_b2c_withdrawals(
            timezone.now() + timedelta(minutes=1), max_attempts=5,
        )
        self.assertEqual(escalated, 0)

        self._deliver(payment, ok=False, conversation_id='CONV-CB-002')

        payment.refresh_from_db()
        self.assertEqual(payment.status, Transaction.Status.FAILED)
        self.assertTrue(
            LedgerEntry.objects.filter(
                reference=f'{payment.id}-REV',
            ).exists()
        )
        self.saving.refresh_from_db()
        self.assertEqual(self.saving.amount, Decimal('10000.00'))

    def test_late_callback_after_escalation_still_resolves(self):
        payment, mpesa = _reserve_withdrawal_fixture(
            membership=self.membership,
            saving=self.saving,
            provider=self.provider,
            reference='WDR-CB-003',
            conversation_id='CONV-CB-003',
        )
        escalated = _reconcile_stale_b2c_withdrawals(
            timezone.now() + timedelta(minutes=1), max_attempts=1,
        )
        self.assertEqual(escalated, 1)
        payment.refresh_from_db()
        self.assertEqual(payment.status, Transaction.Status.UNDER_REVIEW)

        self._deliver(payment, ok=True, conversation_id='CONV-CB-003')

        payment.refresh_from_db()
        self.assertEqual(payment.status, Transaction.Status.COMPLETED)
        self.assertEqual(
            LedgerEntry.objects.filter(
                category=LedgerEntry.Category.SAVING_WITHDRAWAL,
                membership=self.membership,
            ).count(),
            1,
        )

    def test_duplicate_late_callback_after_resolution_is_a_clean_noop(self):
        payment, mpesa = _reserve_withdrawal_fixture(
            membership=self.membership,
            saving=self.saving,
            provider=self.provider,
            reference='WDR-CB-004',
            conversation_id='CONV-CB-004',
        )
        self._deliver(payment, ok=True, conversation_id='CONV-CB-004')
        payment.refresh_from_db()
        self.assertEqual(payment.status, Transaction.Status.COMPLETED)

        with self.assertLogs('saccosphere.payments', level='INFO') as logs:
            second = self._deliver(
                payment, ok=True, conversation_id='CONV-CB-004',
            )

        info_lines = '\n'.join(logs.output)
        self.assertIn('already processed', info_lines)
        self.assertNotIn('replay', info_lines.lower())
        self.assertNotIn('REPLAY_ATTACK', info_lines)

        second.refresh_from_db()
        self.assertTrue(second.processed)
        payment.refresh_from_db()
        self.assertEqual(payment.status, Transaction.Status.COMPLETED)
        self.assertEqual(
            LedgerEntry.objects.filter(
                category=LedgerEntry.Category.SAVING_WITHDRAWAL,
                membership=self.membership,
            ).count(),
            1,
        )


class WithdrawalReconciliationRaceTest(TransactionTestCase):
    """Cross-connection race: escalation vs a genuine callback delivery.

    Mirrors B2CCallbackConcurrencyRegressionTests: two threads, each on
    its own DB connection, released together on a barrier. One runs
    reconciliation's escalation pass, the other delivers a real (failure)
    result callback for the same transaction. The callback's write is
    unconditional, so it always ends up FAILED regardless of ordering -
    and there must be exactly one reversal, never zero or two.

    Requires PostgreSQL for real row-level locking; see the analogous skip
    reasoning in B2CCallbackConcurrencyRegressionTests.
    """

    def setUp(self):
        (
            self.sacco,
            self.user,
            self.membership,
            self.saving,
            self.provider,
        ) = _make_withdrawal_ready_sacco(
            'WDRRACE', 'wdr-race@example.com', '254712270009',
        )

    def test_escalation_and_callback_race_to_exactly_one_reversal(self):
        if connection.vendor == 'sqlite':
            self.skipTest(
                'SQLite has no real row-level locking (select_for_update '
                'is a no-op); run against PostgreSQL to exercise this '
                'race.'
            )

        payment, mpesa = _reserve_withdrawal_fixture(
            membership=self.membership,
            saving=self.saving,
            provider=self.provider,
            reference='WDR-RACE-001',
            conversation_id='CONV-RACE-001',
        )
        callback = Callback.objects.create(
            transaction=payment,
            provider=self.provider,
            raw_payload={'Result': _b2c_result_failed('CONV-RACE-001')},
        )

        barrier = threading.Barrier(2)
        errors = []

        def run_reconciliation():
            barrier.wait()
            try:
                _reconcile_stale_b2c_withdrawals(
                    timezone.now() + timedelta(minutes=1), max_attempts=1,
                )
            except Exception as exc:  # noqa: BLE001 - asserted below
                errors.append(exc)
            finally:
                connection.close()

        def deliver_callback():
            barrier.wait()
            try:
                process_b2c_callback_task(str(callback.id))
            except Exception as exc:  # noqa: BLE001 - asserted below
                errors.append(exc)
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=2) as executor:
            list(executor.map(
                lambda fn: fn(), [run_reconciliation, deliver_callback],
            ))

        self.assertEqual(errors, [])

        payment.refresh_from_db()
        mpesa.refresh_from_db()
        self.saving.refresh_from_db()

        # Exactly one financial effect: one reversal, balance fully
        # restored, the callback's outcome (not UNDER_REVIEW) wins.
        self.assertEqual(payment.status, Transaction.Status.FAILED)
        self.assertTrue(mpesa.callback_received)
        self.assertEqual(self.saving.amount, Decimal('10000.00'))
        self.assertEqual(
            LedgerEntry.objects.filter(
                reference=f'{payment.id}-REV',
            ).count(),
            1,
        )
        self.assertEqual(
            LedgerEntry.objects.filter(
                category=LedgerEntry.Category.SAVING_WITHDRAWAL,
                membership=self.membership,
            ).count(),
            2,  # original debit + the one reversal, never more.
        )


class StuckWithdrawalWithNoRedeliveryStillRecoversTest(TestCase):
    """Regression test (path 2 of 2 - see payments.tests.
    test_replay_recovery_regression for path 1, the STK redelivery
    case): a withdrawal whose first callback delivery's Celery
    processing permanently failed, and for which Safaricom's own
    redelivery is also lost (or never sent) - the system must not leave
    it silently stuck forever with the balance debited and no path
    forward. Reconciliation is that path: it escalates the transaction
    to UNDER_REVIEW once reconciliation attempts are exhausted,
    independently of whether a Callback row for the stuck first delivery
    ever resolves.
    """

    def setUp(self):
        (
            self.sacco,
            self.user,
            self.membership,
            self.saving,
            self.provider,
        ) = _make_withdrawal_ready_sacco(
            'WDR04', 'wdr-stuck@example.com', '254712270004',
        )

    def test_reconciliation_escalates_when_no_redelivery_ever_arrives(self):
        payment, mpesa = _reserve_withdrawal_fixture(
            membership=self.membership,
            saving=self.saving,
            provider=self.provider,
            reference='WDR-STUCK-001',
            conversation_id='CONV-STUCK-001',
        )
        # A callback arrived once and got stuck in Celery (retries
        # exhausted) - the real exception handler in
        # process_b2c_callback_task never marks a Callback processed on
        # a final failure, so this is exactly the state it leaves
        # behind. No redelivery ever follows.
        Callback.objects.create(
            transaction=payment,
            provider=self.provider,
            raw_payload={
                'Result': {'ConversationID': mpesa.conversation_id},
            },
            processed=False,
        )

        escalated = _reconcile_stale_b2c_withdrawals(
            timezone.now() + timedelta(minutes=1), max_attempts=1,
        )

        self.assertEqual(escalated, 1)
        payment.refresh_from_db()
        self.assertEqual(payment.status, Transaction.Status.UNDER_REVIEW)
        self.assertTrue(
            SystemAuditLog.objects.filter(
                action='SAVINGS_WITHDRAWAL_UNDER_REVIEW',
                resource_id=str(self.saving.id),
            ).exists()
        )
        # Still not a lock: a genuinely late callback (the redelivery
        # finally arriving, or a reconciled state) would still resolve
        # it cleanly from here - see B2CCallbackConcurrencyRegressionTests
        # and test_late_callback_after_escalation_still_resolves above.
