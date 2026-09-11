"""Regression test: replay detection must not block legitimate recovery
from a permanently-failed callback delivery.

is_replay_attack used to reject any second delivery of the same
CheckoutRequestID/ConversationID for 24h purely because "we've seen this
ID before" - never checking whether that first sighting was ever
successfully processed. If Celery processing for the first delivery
failed permanently (retries exhausted), Safaricom's own legitimate
redelivery of that same callback would be rejected as a replay for the
next 24 hours, closing off the only recovery path for STK inflows (B2C
withdrawals additionally self-heal via reconciliation - see
payments.tests.test_withdrawal_reconciliation.
StuckWithdrawalWithNoRedeliveryStillRecoversTest for that path).

This file covers path 1 of 2: the first delivery's task fails
permanently (retries exhausted, Callback never marked processed), and a
legitimate redelivery of the exact same callback must now be accepted
and allowed to complete it - not rejected as REPLAY_ATTACK. Without
payments.integrations.mpesa.security.is_replay_attack's state-aware fix,
this test fails: the old cache-only implementation marks the ID as seen
on the first delivery and blocks the second unconditionally for 24h.
"""

from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from accounts.models import Sacco, User
from ledger.models import LedgerEntry
from payments.models import (
    Callback,
    MpesaTransaction,
    PaymentProvider,
    Transaction,
)
from payments.tasks import process_stk_callback_task
from saccomembership.models import Membership
from services.models import Saving, SavingsType


class RedeliveryAfterPermanentFailureRecoversTest(TestCase):
    """A callback whose first delivery permanently failed in Celery must
    be accepted, not blocked, when it is genuinely redelivered."""

    def setUp(self):
        self.client = APIClient()
        self.sacco = Sacco.objects.create(
            name='Replay Recovery SACCO',
            registration_number='REPLAY-001',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
            membership_type=Sacco.MembershipType.OPEN,
        )
        self.user = User.objects.create_user(
            email='replay-member@example.com',
            phone_number='254712296001',
            password='StrongPass1',
        )
        self.membership = Membership.objects.create(
            user=self.user,
            sacco=self.sacco,
            status=Membership.Status.APPROVED,
            member_number='REPLAY-M-001',
        )
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
        self.provider = PaymentProvider.objects.create(
            name='M-Pesa',
            provider_type=PaymentProvider.ProviderType.MPESA,
            is_active=True,
        )
        self.checkout_request_id = 'ws_CO_REPLAY_RECOVERY_001'
        self.transaction = Transaction.objects.create(
            provider=self.provider,
            user=self.user,
            reference='REPLAY-TXN-001',
            transaction_type=Transaction.TransactionType.DEPOSIT,
            amount=Decimal('500.00'),
            gross_amount=Decimal('505.00'),
            platform_fee=Decimal('5.00'),
            sacco=self.sacco,
            status=Transaction.Status.PENDING,
            description='Replay recovery regression test',
        )
        self.mpesa_transaction = MpesaTransaction.objects.create(
            transaction=self.transaction,
            phone_number='254712296001',
            checkout_request_id=self.checkout_request_id,
            related_saving=self.saving,
        )

    def _callback_body(self):
        return {
            'Body': {
                'stkCallback': {
                    'CheckoutRequestID': self.checkout_request_id,
                    'ResultCode': 0,
                    'ResultDesc': (
                        'The service request is processed successfully.'
                    ),
                    'CallbackMetadata': {
                        'Item': [
                            {'Name': 'Amount', 'Value': 505.00},
                            {
                                'Name': 'MpesaReceiptNumber',
                                'Value': 'REPLAYRCT1',
                            },
                        ],
                    },
                },
            },
        }

    def _stk_callback_url(self):
        return reverse(
            'payments:mpesa-stk-callback',
            kwargs={'callback_token': 'replay-test-token'},
        )

    def _first_delivery_arrives_and_gets_stuck(self):
        """Send the first delivery through the real view - so a
        pre-fix is_replay_attack's cache marker actually gets set, the
        same as it would in production - with process_stk_callback_task.
        delay() mocked so the task is accepted but never actually runs.
        That is exactly what "retries exhausted, permanently failed"
        looks like from the DB's point of view afterwards: a Callback
        row that is persisted and never marked processed.
        """
        with patch('payments.tasks.process_stk_callback_task.delay'):
            response = self.client.post(
                self._stk_callback_url(),
                self._callback_body(),
                format='json',
            )
        self.assertEqual(response.status_code, 200)

        callback = Callback.objects.get(transaction=self.transaction)
        self.assertFalse(callback.processed)
        return callback

    @override_settings(MPESA_CALLBACK_TOKEN='replay-test-token')
    @patch('payments.views.is_safaricom_ip', return_value=True)
    def test_redelivery_is_accepted_and_completes_the_deposit(
        self, _ip_mock,
    ):
        first_callback = self._first_delivery_arrives_and_gets_stuck()

        with patch(
            'payments.tasks.process_stk_callback_task.delay',
        ) as delay_mock:
            response = self.client.post(
                self._stk_callback_url(),
                self._callback_body(),
                format='json',
            )

        # Not rejected as a replay: accepted, persisted, re-enqueued.
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['ResultCode'], 0)
        delay_mock.assert_called_once()

        second_callback = Callback.objects.exclude(
            pk=first_callback.pk,
        ).get(transaction=self.transaction)
        self.assertFalse(second_callback.processed)

        # This time processing actually succeeds - run the task for
        # real, synchronously, exactly as Celery would once it lands.
        process_stk_callback_task(str(second_callback.id))

        self.transaction.refresh_from_db()
        self.mpesa_transaction.refresh_from_db()
        self.saving.refresh_from_db()
        second_callback.refresh_from_db()

        self.assertEqual(
            self.transaction.status, Transaction.Status.COMPLETED,
        )
        self.assertTrue(self.mpesa_transaction.callback_received)
        self.assertEqual(self.saving.amount, Decimal('500.00'))
        self.assertTrue(second_callback.processed)

    @override_settings(MPESA_CALLBACK_TOKEN='replay-test-token')
    @patch('payments.views.is_safaricom_ip', return_value=True)
    def test_stuck_first_callback_reprocessed_later_is_a_clean_noop(
        self, _ip_mock,
    ):
        """Belt and suspenders: this fix relies on the idempotency guards
        deeper in the pipeline, not on replay detection, to prevent a
        double credit. Prove they hold even if the original stuck
        callback is somehow retried after the redelivery already
        completed things."""
        first_callback = self._first_delivery_arrives_and_gets_stuck()

        with patch('payments.tasks.process_stk_callback_task.delay'):
            self.client.post(
                self._stk_callback_url(),
                self._callback_body(),
                format='json',
            )
        second_callback = Callback.objects.exclude(
            pk=first_callback.pk,
        ).get(transaction=self.transaction)
        process_stk_callback_task(str(second_callback.id))

        # A late retry of the original stuck callback.
        process_stk_callback_task(str(first_callback.id))

        self.saving.refresh_from_db()
        first_callback.refresh_from_db()
        self.assertEqual(self.saving.amount, Decimal('500.00'))
        self.assertTrue(first_callback.processed)
        self.assertEqual(
            LedgerEntry.objects.filter(
                category=LedgerEntry.Category.SAVING_DEPOSIT,
            ).count(),
            1,
        )
