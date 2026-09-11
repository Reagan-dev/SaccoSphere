"""reconcile_stale_mpesa_transactions batch cap and backlog draining.

Each stale STK transaction costs one synchronous outbound Daraja HTTP
call, so a single run must not walk an unbounded queryset: it queries
Daraja for at most MPESA_RECONCILIATION_BATCH_SIZE transactions (oldest
first), fetched via .iterator() rather than materialising the whole
match set in memory. A backlog bigger than the cap is not lost - it is
picked up oldest-first by later scheduled runs.
"""

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import Sacco, SaccoPaymentConfig, User
from payments.integrations.mpesa.daraja import DarajaClient
from payments.models import MpesaTransaction, PaymentProvider, Transaction
from payments.tasks import reconcile_stale_mpesa_transactions
from saccomembership.models import Membership
from services.models import Saving, SavingsType


# A non-zero ResponseCode routes _process_daraja_status_response into
# _process_failed_callback, which sets Transaction.status to the terminal
# FAILED - so a reconciled transaction drops out of the
# {PENDING, PROCESSING} filter and cannot be picked up again. That is
# what lets the "nothing processed twice" assertion be a real check
# rather than a tautology.
_DARAJA_FAILURE_RESPONSE = {
    'ResponseCode': '1',
    'ResponseDescription': (
        'The balance is insufficient for the transaction.'
    ),
}


@override_settings(MPESA_RECONCILIATION_BATCH_SIZE=3)
class ReconciliationBatchCapTestCase(TestCase):
    """Seed more stale STK transactions than the per-run cap."""

    STALE_COUNT = 5

    def setUp(self):
        self.provider = PaymentProvider.objects.create(
            name='M-Pesa',
            provider_type=PaymentProvider.ProviderType.MPESA,
            is_active=True,
        )
        self.sacco = Sacco.objects.create(
            name='Batch Cap SACCO',
            registration_number='BATCHCAP01',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
            is_active=True,
        )
        SaccoPaymentConfig.objects.create(
            sacco=self.sacco,
            shortcode_type=SaccoPaymentConfig.ShortcodeType.PAYBILL,
            shortcode='600888',
            stk_passkey='batch_passkey',
            daraja_consumer_key='batch_consumer_key',
            daraja_consumer_secret='batch_consumer_secret',
            environment=SaccoPaymentConfig.Environment.SANDBOX,
            is_active=True,
        )
        self.user = User.objects.create_user(
            email='batch-cap-member@example.com',
            phone_number='254712610001',
            password='StrongPass1',
        )
        membership = Membership.objects.create(
            user=self.user,
            sacco=self.sacco,
            status=Membership.Status.APPROVED,
            member_number='BATCHCAP-M-001',
        )
        savings_type = SavingsType.objects.create(
            sacco=self.sacco,
            name=SavingsType.Name.BOSA,
            minimum_contribution=Decimal('100.00'),
        )
        saving = Saving.objects.create(
            membership=membership,
            savings_type=savings_type,
            amount=Decimal('100.00'),
            total_contributions=Decimal('100.00'),
            status=Saving.Status.ACTIVE,
        )

        # Oldest-first, one minute apart, all well past the (default
        # 5-minute) reconciliation threshold - deterministic ordering so
        # "the cap takes the oldest N" is an exact, not approximate,
        # assertion.
        self.mpesa_transactions = []
        base = timezone.now() - timedelta(minutes=100)
        for i in range(self.STALE_COUNT):
            transaction = Transaction.objects.create(
                provider=self.provider,
                user=self.user,
                reference=f'SS-BATCHCAP-{i}',
                transaction_type=Transaction.TransactionType.DEPOSIT,
                amount=Decimal('100.00'),
                sacco=self.sacco,
                status=Transaction.Status.PENDING,
                description='Batch cap reconciliation test',
            )
            mpesa = MpesaTransaction.objects.create(
                transaction=transaction,
                phone_number='254712610001',
                transaction_type=MpesaTransaction.TransactionType.STK_PUSH,
                checkout_request_id=f'ws_CO_BATCHCAP_{i}',
                related_saving=saving,
            )
            MpesaTransaction.objects.filter(pk=mpesa.pk).update(
                created_at=base + timedelta(minutes=i),
            )
            mpesa.refresh_from_db()
            self.mpesa_transactions.append(mpesa)

    def _run_with_mocked_daraja(self):
        queried = []

        def _query(client, checkout_request_id):
            queried.append(checkout_request_id)
            return dict(_DARAJA_FAILURE_RESPONSE)

        with patch.object(
            DarajaClient,
            'query_stk_status',
            autospec=True,
            side_effect=_query,
        ):
            result = reconcile_stale_mpesa_transactions()
        return result, queried

    def test_first_run_processes_only_up_to_the_cap_oldest_first(self):
        result, queried = self._run_with_mocked_daraja()

        self.assertEqual(result['found'], 3)
        self.assertEqual(result['processed'], 3)
        # "reconciled" means a definitive Daraja response was obtained and
        # processed - which happens here even though the M-Pesa payment
        # itself is a business failure (non-zero ResponseCode); that
        # outcome lands on the Transaction's own status, checked below.
        self.assertEqual(result['reconciled'], 3)
        self.assertEqual(result['failed'], 0)
        self.assertEqual(result['skipped'], 0)
        self.assertEqual(result['remaining'], 2)
        self.assertEqual(len(queried), 3)

        # The three oldest (indices 0, 1, 2), in oldest-first order.
        self.assertEqual(
            queried,
            ['ws_CO_BATCHCAP_0', 'ws_CO_BATCHCAP_1', 'ws_CO_BATCHCAP_2'],
        )

        statuses = {
            mt.checkout_request_id: Transaction.objects.get(
                pk=mt.transaction_id,
            ).status
            for mt in self.mpesa_transactions
        }
        for i in range(3):
            self.assertEqual(
                statuses[f'ws_CO_BATCHCAP_{i}'], Transaction.Status.FAILED,
            )
        for i in range(3, 5):
            self.assertEqual(
                statuses[f'ws_CO_BATCHCAP_{i}'], Transaction.Status.PENDING,
            )

    def test_second_run_picks_up_the_remainder_with_nothing_twice(self):
        first_result, first_queried = self._run_with_mocked_daraja()
        self.assertEqual(first_result['found'], 3)
        self.assertEqual(first_result['remaining'], 2)

        second_result, second_queried = self._run_with_mocked_daraja()

        self.assertEqual(second_result['found'], 2)
        self.assertEqual(second_result['processed'], 2)
        self.assertEqual(second_result['reconciled'], 2)
        self.assertEqual(second_result['failed'], 0)
        self.assertEqual(second_result['skipped'], 0)
        self.assertEqual(second_result['remaining'], 0)
        self.assertEqual(
            second_queried,
            ['ws_CO_BATCHCAP_3', 'ws_CO_BATCHCAP_4'],
        )

        # No checkout_request_id was ever queried in both runs.
        self.assertEqual(
            set(first_queried) & set(second_queried),
            set(),
        )
        # Together, every seeded transaction was queried exactly once.
        self.assertEqual(
            sorted(first_queried + second_queried),
            sorted(
                f'ws_CO_BATCHCAP_{i}' for i in range(self.STALE_COUNT)
            ),
        )

        for mt in self.mpesa_transactions:
            self.assertEqual(
                Transaction.objects.get(pk=mt.transaction_id).status,
                Transaction.Status.FAILED,
            )

        # A third run has nothing left to do.
        third_result, third_queried = self._run_with_mocked_daraja()
        self.assertEqual(third_result['found'], 0)
        self.assertEqual(third_result['remaining'], 0)
        self.assertEqual(third_queried, [])
