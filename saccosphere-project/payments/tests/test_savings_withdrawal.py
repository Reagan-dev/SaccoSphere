"""Savings withdrawal: double-spend + idempotency + B2C callback hardening.

Covers the reserve-under-lock flow in payments/withdrawals.py:
  * balance re-read under select_for_update (no trust in the passed
    instance) -> two racing full-balance requests, exactly one payout;
  * explicit idempotency key + in-flight window guard -> a retried POST
    returns the original instead of a second B2C call;
  * per-transaction B2C ceiling; zero/negative amount; FROZEN account;
  * wrong-tenant rejection;
  * DarajaError at initiation reverses balance + posts an offsetting
    ledger entry, idempotently;

and the B2C callback processors in payments/tasks.py:
  * _process_successful_b2c_callback with related_saving -> COMPLETED,
    ledger debit, fee invoice line item, notification, nothing raised;
  * _process_failed_b2c_callback -> balance revert + offsetting entry
    survive even when the notification helper is made to raise.
"""

import threading
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from unittest.mock import patch

from django.db import connection
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from rest_framework.test import APIClient

from accounts.models import Sacco, SaccoPaymentConfig, User
from billing.models import InvoiceLineItem
from ledger.models import LedgerEntry
from ledger.utils import create_ledger_entry
from notifications.models import Notification
from payments.integrations.mpesa.daraja import DarajaError
from payments.models import MpesaTransaction, Transaction
from payments.tasks import (
    _process_failed_b2c_callback,
    _process_successful_b2c_callback,
)
from payments.withdrawals import (
    MAX_B2C_WITHDRAWAL_AMOUNT,
    _reverse_withdrawal,
    initiate_savings_withdrawal,
)
from saccomanagement.models import SystemAuditLog
from saccomembership.models import Membership
from services.models import Saving, SavingsType


_B2C_OK = {
    'ConversationID': 'CONV-1',
    'OriginatorConversationID': 'ORIG-1',
}


def _make_withdrawal_ready_sacco(reg, email, phone, balance):
    sacco = Sacco.objects.create(
        name=f'WD {reg}',
        registration_number=reg,
        sector=Sacco.Sector.FINANCE,
        county='Nairobi',
        membership_type=Sacco.MembershipType.OPEN,
        payment_ready=True,
    )
    SaccoPaymentConfig.objects.create(
        sacco=sacco,
        shortcode_type=SaccoPaymentConfig.ShortcodeType.PAYBILL,
        shortcode='600111',
        stk_passkey='wd_passkey',
        daraja_consumer_key='wd_consumer_key',
        daraja_consumer_secret='wd_consumer_secret',
        environment=SaccoPaymentConfig.Environment.SANDBOX,
        b2c_initiator_name='wd_initiator',
        b2c_security_credential='wd_security_credential',
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
        amount=Decimal(balance),
        total_contributions=Decimal(balance),
        status=Saving.Status.ACTIVE,
    )
    return sacco, user, membership, saving


class _FixtureMixin:
    def setUp(self):
        (
            self.sacco,
            self.user,
            self.membership,
            self.saving,
        ) = _make_withdrawal_ready_sacco(
            'WD01',
            'wd-member@example.com',
            '254712240001',
            '10000.00',
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.url = reverse('payments:mpesa-b2c-withdraw')

    def _post(self, **overrides):
        body = {
            'phone_number': '254712240001',
            'amount': '5000.00',
            'sacco_id': str(self.sacco.id),
            'saving_id': str(self.saving.id),
        }
        body.update(overrides)
        return self.client.post(self.url, body, format='json')


class SavingsWithdrawalHappyPathTest(_FixtureMixin, TestCase):
    def test_withdrawal_reserves_balance_and_writes_ledger_at_initiation(self):
        with patch('payments.withdrawals.DarajaClient') as client_cls:
            client = client_cls.return_value
            client._build_callback_url.return_value = 'https://cb.test/b2c'
            client.initiate_b2c.return_value = _B2C_OK

            response = self._post(amount='5000.00')

        self.assertEqual(response.status_code, 201)
        self.assertEqual(client.initiate_b2c.call_count, 1)
        # Net (gross 5000 - tiered fee 25) is what Daraja is asked to send.
        _args, kwargs = client.initiate_b2c.call_args
        self.assertEqual(kwargs['amount'], Decimal('4975.00'))

        self.saving.refresh_from_db()
        self.assertEqual(self.saving.amount, Decimal('5000.00'))
        self.assertEqual(self.saving.total_withdrawals, Decimal('5000.00'))

        payment = Transaction.objects.get(
            transaction_type=Transaction.TransactionType.WITHDRAWAL,
        )
        self.assertEqual(payment.status, Transaction.Status.SENT)
        self.assertEqual(payment.external_reference, 'CONV-1')
        self.assertEqual(payment.amount, Decimal('4975.00'))
        self.assertEqual(payment.gross_amount, Decimal('5000.00'))

        mpesa = MpesaTransaction.objects.get(transaction=payment)
        self.assertEqual(mpesa.conversation_id, 'CONV-1')
        self.assertEqual(
            mpesa.transaction_type, MpesaTransaction.TransactionType.B2C,
        )

        debit = LedgerEntry.objects.get(reference=str(payment.id))
        self.assertEqual(debit.entry_type, LedgerEntry.EntryType.DEBIT)
        self.assertEqual(
            debit.category, LedgerEntry.Category.SAVING_WITHDRAWAL,
        )
        self.assertEqual(debit.amount, Decimal('5000.00'))
        self.assertEqual(debit.membership_id, self.membership.id)

        self.assertTrue(
            SystemAuditLog.objects.filter(
                user=self.user,
                action='SAVINGS_WITHDRAWAL_INITIATED',
                resource_type='Saving',
                resource_id=str(self.saving.id),
            ).exists()
        )


class SavingsWithdrawalValidationTest(_FixtureMixin, TestCase):
    def test_zero_amount_rejected(self):
        response = self._post(amount='0.00')
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Transaction.objects.exists())

    def test_negative_amount_rejected(self):
        response = self._post(amount='-100.00')
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Transaction.objects.exists())

    def test_over_ceiling_rejected_by_serializer(self):
        response = self._post(
            amount=str(MAX_B2C_WITHDRAWAL_AMOUNT + Decimal('1.00')),
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Transaction.objects.exists())

    def test_over_ceiling_rejected_in_helper_even_bypassing_serializer(self):
        with patch('payments.withdrawals.DarajaClient') as client_cls:
            ok, payload, http_status = initiate_savings_withdrawal(
                saving=self.saving,
                phone_number='+254712240001',
                requested_amount=Decimal('300000.00'),
            )
        self.assertFalse(ok)
        self.assertEqual(http_status, 400)
        client_cls.return_value.initiate_b2c.assert_not_called()
        self.saving.refresh_from_db()
        self.assertEqual(self.saving.amount, Decimal('10000.00'))

    def test_frozen_account_rejected(self):
        self.saving.status = Saving.Status.FROZEN
        self.saving.save(update_fields=['status'])

        with patch('payments.withdrawals.DarajaClient') as client_cls:
            ok, payload, http_status = initiate_savings_withdrawal(
                saving=Saving.objects.get(id=self.saving.id),
                phone_number='+254712240001',
                requested_amount=Decimal('1000.00'),
            )

        self.assertFalse(ok)
        self.assertEqual(http_status, 400)
        self.assertEqual(payload['account_status'], Saving.Status.FROZEN)
        client_cls.return_value.initiate_b2c.assert_not_called()
        self.assertFalse(LedgerEntry.objects.exists())
        self.saving.refresh_from_db()
        self.assertEqual(self.saving.amount, Decimal('10000.00'))

    def test_amount_not_exceeding_fee_rejected(self):
        response = self._post(amount='10.00')
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Transaction.objects.exists())

    def test_early_rejection_branch_emits_a_structured_log(self):
        self.saving.status = Saving.Status.FROZEN
        self.saving.save(update_fields=['status'])

        with self.assertLogs('saccosphere.payments', level='WARNING') as logs:
            with patch('payments.withdrawals.DarajaClient'):
                ok, _payload, http_status = initiate_savings_withdrawal(
                    saving=Saving.objects.get(id=self.saving.id),
                    phone_number='+254712240001',
                    requested_amount=Decimal('1000.00'),
                )

        self.assertFalse(ok)
        self.assertEqual(http_status, 400)
        line = '\n'.join(logs.output)
        self.assertIn('not ACTIVE', line)
        self.assertIn(str(self.saving.id), line)
        self.assertIn(str(self.sacco.id), line)
        self.assertIn(str(self.user.id), line)


class SavingsWithdrawalTenantScopingTest(_FixtureMixin, TestCase):
    def setUp(self):
        super().setUp()
        (
            self.other_sacco,
            self.other_user,
            self.other_membership,
            self.other_saving,
        ) = _make_withdrawal_ready_sacco(
            'WD02',
            'wd-other@example.com',
            '254712240002',
            '9000.00',
        )

    def test_cannot_withdraw_from_another_members_saving(self):
        # Authenticated as self.user, but pointing at the other member's
        # saving + sacco.
        response = self.client.post(
            self.url,
            {
                'phone_number': '254712240001',
                'amount': '1000.00',
                'sacco_id': str(self.other_sacco.id),
                'saving_id': str(self.other_saving.id),
            },
            format='json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('your own saving', response.data['detail'])
        self.assertFalse(Transaction.objects.exists())
        self.other_saving.refresh_from_db()
        self.assertEqual(self.other_saving.amount, Decimal('9000.00'))

    def test_saving_sacco_mismatch_rejected(self):
        response = self.client.post(
            self.url,
            {
                'phone_number': '254712240001',
                'amount': '1000.00',
                'sacco_id': str(self.other_sacco.id),
                'saving_id': str(self.saving.id),
            },
            format='json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Transaction.objects.exists())


class SavingsWithdrawalIdempotencyTest(_FixtureMixin, TestCase):
    def test_same_idempotency_key_returns_original_no_second_payout(self):
        with patch('payments.withdrawals.DarajaClient') as client_cls:
            client = client_cls.return_value
            client._build_callback_url.return_value = 'https://cb.test/b2c'
            client.initiate_b2c.return_value = _B2C_OK

            first = self._post(amount='5000.00', idempotency_key='req-1')
            second = self._post(amount='5000.00', idempotency_key='req-1')

            self.assertEqual(client.initiate_b2c.call_count, 1)

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.data['duplicate'])

        self.assertEqual(
            Transaction.objects.filter(
                transaction_type=Transaction.TransactionType.WITHDRAWAL,
            ).count(),
            1,
        )
        self.assertEqual(
            LedgerEntry.objects.filter(
                category=LedgerEntry.Category.SAVING_WITHDRAWAL,
            ).count(),
            1,
        )
        self.saving.refresh_from_db()
        self.assertEqual(self.saving.amount, Decimal('5000.00'))

    def test_in_flight_window_dedupes_without_a_client_key(self):
        with patch('payments.withdrawals.DarajaClient') as client_cls:
            client = client_cls.return_value
            client._build_callback_url.return_value = 'https://cb.test/b2c'
            client.initiate_b2c.return_value = _B2C_OK

            first = self._post(amount='5000.00')
            second = self._post(amount='5000.00')

            self.assertEqual(client.initiate_b2c.call_count, 1)

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.data['duplicate'])
        self.assertEqual(
            Transaction.objects.filter(
                transaction_type=Transaction.TransactionType.WITHDRAWAL,
            ).count(),
            1,
        )


class SavingsWithdrawalStaleInstanceTest(_FixtureMixin, TestCase):
    """The specific bug: the passed-in Saving instance must not be trusted."""

    def setUp(self):
        super().setUp()
        self.saving.amount = Decimal('6000.00')
        self.saving.total_contributions = Decimal('6000.00')
        self.saving.save(
            update_fields=['amount', 'total_contributions'],
        )

    def test_second_call_with_stale_instance_is_rejected_under_lock(self):
        stale = Saving.objects.get(id=self.saving.id)  # amount == 6000

        with patch('payments.withdrawals.DarajaClient') as client_cls:
            client = client_cls.return_value
            client._build_callback_url.return_value = 'https://cb.test/b2c'
            client.initiate_b2c.return_value = _B2C_OK

            ok_1, _p1, s1 = initiate_savings_withdrawal(
                saving=stale,
                phone_number='+254712240001',
                requested_amount=Decimal('6000.00'),
                idempotency_key='call-1',
            )

            # Simulate the first withdrawal's B2C callback landing, so the
            # in-flight window guard no longer masks the balance check.
            Transaction.objects.filter(
                transaction_type=Transaction.TransactionType.WITHDRAWAL,
            ).update(status=Transaction.Status.COMPLETED)

            # Reuse the SAME stale instance whose .amount is still 6000.
            # The buggy code trusted stale.amount and would double-spend;
            # the fix re-reads the locked row (now 0) and rejects.
            ok_2, p2, s2 = initiate_savings_withdrawal(
                saving=stale,
                phone_number='+254712240001',
                requested_amount=Decimal('1000.00'),
                idempotency_key='call-2',
            )

            self.assertEqual(client.initiate_b2c.call_count, 1)

        self.assertTrue(ok_1)
        self.assertEqual(s1, 201)
        self.assertFalse(ok_2)
        self.assertEqual(s2, 400)
        self.assertIn('Insufficient', p2['error'])

        self.saving.refresh_from_db()
        self.assertEqual(self.saving.amount, Decimal('0.00'))
        self.assertEqual(
            LedgerEntry.objects.filter(
                category=LedgerEntry.Category.SAVING_WITHDRAWAL,
            ).count(),
            1,
        )


class SavingsWithdrawalDarajaErrorReversalTest(_FixtureMixin, TestCase):
    def test_daraja_error_reverses_balance_and_posts_offsetting_entry(self):
        with self.assertLogs('saccosphere.payments', level='ERROR') as logs:
            with patch('payments.withdrawals.DarajaClient') as client_cls:
                client = client_cls.return_value
                client._build_callback_url.return_value = (
                    'https://cb.test/b2c'
                )
                client.initiate_b2c.side_effect = DarajaError(
                    'B2C rejected', response_code='500',
                )

                ok, payload, http_status = initiate_savings_withdrawal(
                    saving=self.saving,
                    phone_number='+254712240001',
                    requested_amount=Decimal('5000.00'),
                    idempotency_key='rev-1',
                )

        # Clean error, never a bare 500.
        self.assertFalse(ok)
        self.assertEqual(http_status, 502)
        self.assertEqual(payload['response_code'], '500')

        # A structured failure log carrying member / saving / txn / code.
        error_line = '\n'.join(
            m for m in logs.output if m.startswith('ERROR')
        )
        self.assertIn('B2C initiation failed', error_line)
        self.assertIn('code=500', error_line)
        self.assertIn(str(self.saving.id), error_line)
        self.assertIn(str(self.sacco.id), error_line)
        self.assertIn(str(self.user.id), error_line)

        self.saving.refresh_from_db()
        self.assertEqual(self.saving.amount, Decimal('10000.00'))
        self.assertEqual(self.saving.total_withdrawals, Decimal('0.00'))

        payment = Transaction.objects.get(
            transaction_type=Transaction.TransactionType.WITHDRAWAL,
        )
        self.assertEqual(payment.status, Transaction.Status.FAILED)
        self.assertIn('withdrawal_reversal_reason', payment.metadata)

        mpesa = MpesaTransaction.objects.get(transaction=payment)
        self.assertEqual(mpesa.result_code, '500')

        debit = LedgerEntry.objects.get(reference=str(payment.id))
        credit = LedgerEntry.objects.get(reference=f'{payment.id}-REV')
        self.assertEqual(debit.entry_type, LedgerEntry.EntryType.DEBIT)
        self.assertEqual(credit.entry_type, LedgerEntry.EntryType.CREDIT)
        self.assertEqual(credit.category, LedgerEntry.Category.ADJUSTMENT)
        self.assertEqual(debit.amount, credit.amount)

        # Reversal is idempotent.
        _reverse_withdrawal(payment, reason='second call', response_code=None)
        self.assertEqual(
            LedgerEntry.objects.filter(
                reference=f'{payment.id}-REV',
            ).count(),
            1,
        )
        self.saving.refresh_from_db()
        self.assertEqual(self.saving.amount, Decimal('10000.00'))


class ConcurrentSavingsWithdrawalRaceTest(TransactionTestCase):
    """Two full-balance requests race; exactly one payout, one debit.

    Requires PostgreSQL: on SQLite select_for_update() is a no-op and
    concurrent writers raise 'database is locked', so the race cannot be
    represented faithfully. The deterministic guard is covered by
    SavingsWithdrawalStaleInstanceTest / SavingsWithdrawalIdempotencyTest
    on every backend.
    """

    def setUp(self):
        (
            self.sacco,
            self.user,
            self.membership,
            self.saving,
        ) = _make_withdrawal_ready_sacco(
            'WDRACE',
            'wd-race@example.com',
            '254712240009',
            '6000.00',
        )

    def test_concurrent_full_balance_requests_fire_one_payout(self):
        if connection.vendor == 'sqlite':
            self.skipTest(
                'SQLite has no real row-level locking (select_for_update '
                'is a no-op) and serialises writers with "database is '
                'locked"; run against PostgreSQL to exercise this race.'
            )

        with patch('payments.withdrawals.DarajaClient') as client_cls:
            client = client_cls.return_value
            client._build_callback_url.return_value = 'https://cb.test/b2c'
            client.initiate_b2c.return_value = _B2C_OK

            barrier = threading.Barrier(2)
            results = []
            errors = []

            def fire(idx):
                barrier.wait()
                try:
                    results.append(
                        initiate_savings_withdrawal(
                            saving=Saving.objects.get(id=self.saving.id),
                            phone_number='+254712240009',
                            requested_amount=Decimal('6000.00'),
                            idempotency_key=f'race-{idx}',
                        )
                    )
                except Exception as exc:  # noqa: BLE001 - asserted below
                    errors.append(exc)
                finally:
                    connection.close()

            with ThreadPoolExecutor(max_workers=2) as executor:
                list(executor.map(fire, range(2)))

            self.assertEqual(errors, [])
            self.assertEqual(client.initiate_b2c.call_count, 1)

        statuses = sorted(http_status for _ok, _p, http_status in results)
        self.assertEqual(statuses, [201, 400])

        self.assertEqual(
            Transaction.objects.filter(
                transaction_type=Transaction.TransactionType.WITHDRAWAL,
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
            LedgerEntry.objects.filter(
                category=LedgerEntry.Category.SAVING_WITHDRAWAL,
            ).count(),
            1,
        )
        self.saving.refresh_from_db()
        self.assertEqual(self.saving.amount, Decimal('0.00'))
        self.assertEqual(self.saving.total_withdrawals, Decimal('6000.00'))


def _b2c_result_ok(conversation_id, receipt='QWE12345XY'):
    """Realistic unwrapped Daraja B2C ``Result`` payload (success)."""
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
                {
                    'Key': 'ReceiverPartyPublicName',
                    'Value': '254712240001 - WD Member',
                },
            ],
        },
        'ReferenceData': {
            'ReferenceItem': {
                'Key': 'QueueTimeoutURL',
                'Value': 'https://cb.test/timeout',
            },
        },
    }


def _b2c_result_failed(conversation_id, code=2001):
    """Realistic unwrapped Daraja B2C ``Result`` payload (failure)."""
    return {
        'ResultType': 0,
        'ResultCode': code,
        'ResultDesc': 'The initiator information is invalid.',
        'OriginatorConversationID': f'ORIG-{conversation_id}',
        'ConversationID': conversation_id,
        'TransactionID': '',
    }


class SavingsWithdrawalB2CCallbackTest(_FixtureMixin, TestCase):
    """Drive the real B2C callback processors with related_saving set."""

    def _reserve(self, *, conversation_id, gross='5000.00'):
        """Reserve a withdrawal via the real initiation path."""
        # Seed a prior ledger CREDIT so balance_after is a clean figure.
        create_ledger_entry(
            membership=self.membership,
            entry_type=LedgerEntry.EntryType.CREDIT,
            category=LedgerEntry.Category.SAVING_DEPOSIT,
            amount=Decimal('10000.00'),
            description='Opening deposit (test seed).',
            reference=f'SEED-{conversation_id}',
        )
        with patch('payments.withdrawals.DarajaClient') as client_cls:
            client = client_cls.return_value
            client._build_callback_url.return_value = 'https://cb.test/b2c'
            client.initiate_b2c.return_value = {
                'ConversationID': conversation_id,
                'OriginatorConversationID': f'ORIG-{conversation_id}',
            }
            ok, _payload, http_status = initiate_savings_withdrawal(
                saving=Saving.objects.get(id=self.saving.id),
                phone_number='+254712240001',
                requested_amount=Decimal(gross),
                idempotency_key=f'cb-{conversation_id}',
            )
        self.assertTrue(ok)
        self.assertEqual(http_status, 201)
        payment = Transaction.objects.get(external_reference=conversation_id)
        mpesa = MpesaTransaction.objects.get(transaction=payment)
        return payment, mpesa

    def test_successful_callback_completes_without_raising(self):
        payment, mpesa = self._reserve(conversation_id='CONV-WD-OK')
        # Gross 5000 was already debited at initiation.
        self.saving.refresh_from_db()
        self.assertEqual(self.saving.amount, Decimal('5000.00'))

        with self.captureOnCommitCallbacks(execute=True):
            _process_successful_b2c_callback(
                mpesa,
                payment,
                _b2c_result_ok('CONV-WD-OK'),
            )

        payment.refresh_from_db()
        self.assertEqual(payment.status, Transaction.Status.COMPLETED)
        self.assertEqual(payment.external_reference, 'QWE12345XY')

        debit = LedgerEntry.objects.get(
            reference=str(payment.id),
            category=LedgerEntry.Category.SAVING_WITHDRAWAL,
        )
        self.assertEqual(debit.entry_type, LedgerEntry.EntryType.DEBIT)
        self.assertEqual(debit.amount, Decimal('5000.00'))
        # 10,000 seeded credit - 5,000 withdrawal.
        self.assertEqual(debit.balance_after, Decimal('5000.00'))

        line_item = InvoiceLineItem.objects.get(transaction=payment)
        self.assertEqual(line_item.sacco_id, self.sacco.id)
        self.assertEqual(line_item.transaction_type, 'withdrawal')
        self.assertEqual(line_item.gross_amount, Decimal('5000.00'))

        note = Notification.objects.get(
            user=self.user,
            category=Notification.Category.PAYMENT,
            related_object_type='MpesaTransaction',
            related_object_id=str(mpesa.id),
        )
        self.assertEqual(note.title, 'Withdrawal successful')

    def test_successful_callback_survives_a_broken_notification(self):
        payment, mpesa = self._reserve(conversation_id='CONV-WD-OK2')

        with patch(
            'payments.tasks._notify_withdrawal_success',
            side_effect=RuntimeError('notify boom'),
        ):
            with self.captureOnCommitCallbacks(execute=True):
                _process_successful_b2c_callback(
                    mpesa,
                    payment,
                    _b2c_result_ok('CONV-WD-OK2'),
                )

        payment.refresh_from_db()
        self.assertEqual(payment.status, Transaction.Status.COMPLETED)
        self.assertTrue(
            LedgerEntry.objects.filter(
                reference=str(payment.id),
                category=LedgerEntry.Category.SAVING_WITHDRAWAL,
            ).exists()
        )
        self.assertTrue(
            InvoiceLineItem.objects.filter(transaction=payment).exists()
        )

    def test_failed_callback_reverts_balance_and_posts_offset(self):
        payment, mpesa = self._reserve(conversation_id='CONV-WD-FAIL')
        self.saving.refresh_from_db()
        self.assertEqual(self.saving.amount, Decimal('5000.00'))

        with self.captureOnCommitCallbacks(execute=True):
            _process_failed_b2c_callback(
                mpesa,
                payment,
                _b2c_result_failed('CONV-WD-FAIL'),
                2001,
            )

        payment.refresh_from_db()
        self.assertEqual(payment.status, Transaction.Status.FAILED)

        self.saving.refresh_from_db()
        self.assertEqual(self.saving.amount, Decimal('10000.00'))
        self.assertEqual(self.saving.total_withdrawals, Decimal('0.00'))

        debit = LedgerEntry.objects.get(reference=str(payment.id))
        credit = LedgerEntry.objects.get(reference=f'{payment.id}-REV')
        self.assertEqual(debit.entry_type, LedgerEntry.EntryType.DEBIT)
        self.assertEqual(credit.entry_type, LedgerEntry.EntryType.CREDIT)
        self.assertEqual(credit.category, LedgerEntry.Category.ADJUSTMENT)
        self.assertEqual(debit.amount, credit.amount)

        note = Notification.objects.get(
            user=self.user,
            category=Notification.Category.PAYMENT,
            related_object_id=str(mpesa.id),
        )
        self.assertEqual(note.title, 'Withdrawal failed')

    def test_failed_callback_revert_survives_broken_notification(self):
        payment, mpesa = self._reserve(conversation_id='CONV-WD-FAIL2')

        with patch(
            'payments.tasks._notify_withdrawal_failure',
            side_effect=RuntimeError('notify boom'),
        ):
            with self.captureOnCommitCallbacks(execute=True):
                _process_failed_b2c_callback(
                    mpesa,
                    payment,
                    _b2c_result_failed('CONV-WD-FAIL2'),
                    2001,
                )

        payment.refresh_from_db()
        self.assertEqual(payment.status, Transaction.Status.FAILED)
        self.saving.refresh_from_db()
        self.assertEqual(self.saving.amount, Decimal('10000.00'))
        self.assertTrue(
            LedgerEntry.objects.filter(
                reference=f'{payment.id}-REV',
            ).exists()
        )
