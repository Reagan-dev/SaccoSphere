"""Compliance audit trail for real deposit / withdrawal money movement.

Deposits and withdrawals now emit ``SystemAuditLog`` rows at their
terminal outcome (and, for withdrawals, at initiation - already present),
matching the detail of the dividend audit calls: actor, saving id,
amount, and the Transaction / LedgerEntry cross-reference. Rows are
queryable per-SACCO via ``new_values__sacco_id`` like the dividend ones.
"""

from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from payments.models import MpesaTransaction, PaymentProvider, Transaction
from payments.tasks import (
    _apply_saving_deposit,
    _process_failed_b2c_callback,
    _process_successful_b2c_callback,
)
from payments.withdrawals import _reverse_withdrawal
from saccomanagement.models import SystemAuditLog
from services.models import Saving

from .test_savings_withdrawal import (
    _b2c_result_failed,
    _b2c_result_ok,
    _make_withdrawal_ready_sacco,
)


class SavingsDepositAuditTest(TestCase):
    def setUp(self):
        self.sacco, self.user, self.membership, self.saving = (
            _make_withdrawal_ready_sacco(
                'DAUD1', 'daud-m@example.com', '254712250001', '0.00',
            )
        )
        self.provider = PaymentProvider.objects.create(
            name='M-Pesa',
            provider_type=PaymentProvider.ProviderType.MPESA,
            is_active=True,
        )

    def _deposit(self, amount, reference, *, saving=None):
        saving = saving or self.saving
        transaction = Transaction.objects.create(
            provider=self.provider,
            user=self.user,
            reference=reference,
            transaction_type=Transaction.TransactionType.DEPOSIT,
            amount=Decimal(amount),
            sacco=self.sacco,
            status=Transaction.Status.COMPLETED,
            description='deposit audit test',
        )
        mpesa = MpesaTransaction.objects.create(
            transaction=transaction,
            phone_number='254712250001',
            checkout_request_id=f'CR-{reference}',
            mpesa_receipt_number=f'RCT-{reference}',
            related_saving=saving,
        )
        _apply_saving_deposit(mpesa, transaction, Decimal(amount))
        return transaction

    def test_successful_deposit_writes_completed_audit(self):
        transaction = self._deposit('750.00', 'DEP-A')

        audit = SystemAuditLog.objects.get(
            user=self.user,
            action='SAVINGS_DEPOSIT_COMPLETED',
            resource_type='Saving',
            resource_id=str(self.saving.id),
        )
        self.assertEqual(audit.new_values['sacco_id'], str(self.sacco.id))
        self.assertEqual(
            audit.new_values['membership_id'], str(self.membership.id),
        )
        self.assertEqual(
            audit.new_values['transaction_id'], str(transaction.id),
        )
        self.assertEqual(audit.new_values['net_amount'], '750.00')
        self.assertIsNotNone(audit.new_values['ledger_entry_id'])
        self.assertEqual(
            audit.new_values['ledger_entry_reference'], str(transaction.id),
        )
        self.assertFalse(audit.new_values['posted_to_inactive_account'])

    def test_deposit_into_frozen_account_flags_and_still_audits_completion(
        self,
    ):
        Saving.objects.filter(pk=self.saving.pk).update(
            status=Saving.Status.FROZEN,
        )

        self._deposit('500.00', 'DEP-FROZEN')

        completed = SystemAuditLog.objects.get(
            action='SAVINGS_DEPOSIT_COMPLETED',
            resource_id=str(self.saving.id),
        )
        self.assertTrue(completed.new_values['posted_to_inactive_account'])
        self.assertTrue(
            SystemAuditLog.objects.filter(
                action='DEPOSIT_INTO_INACTIVE_ACCOUNT',
                resource_id=str(self.saving.id),
            ).exists()
        )

    def test_deposit_audit_is_queryable_per_sacco(self):
        other_sacco, other_user, _omb, other_saving = (
            _make_withdrawal_ready_sacco(
                'DAUD2', 'daud-o@example.com', '254712250002', '0.00',
            )
        )
        self._deposit('100.00', 'DEP-A2')
        # Second deposit belongs to the other SACCO.
        other_txn = Transaction.objects.create(
            provider=self.provider,
            user=other_user,
            reference='DEP-B2',
            transaction_type=Transaction.TransactionType.DEPOSIT,
            amount=Decimal('200.00'),
            sacco=other_sacco,
            status=Transaction.Status.COMPLETED,
            description='deposit audit test',
        )
        other_mpesa = MpesaTransaction.objects.create(
            transaction=other_txn,
            phone_number='254712250002',
            checkout_request_id='CR-DEP-B2',
            related_saving=other_saving,
        )
        _apply_saving_deposit(other_mpesa, other_txn, Decimal('200.00'))

        a_rows = SystemAuditLog.objects.filter(
            action='SAVINGS_DEPOSIT_COMPLETED',
            new_values__sacco_id=str(self.sacco.id),
        )
        self.assertEqual(a_rows.count(), 1)
        self.assertEqual(
            a_rows.first().resource_id, str(self.saving.id),
        )
        self.assertEqual(
            SystemAuditLog.objects.filter(
                action='SAVINGS_DEPOSIT_COMPLETED',
                new_values__sacco_id=str(other_sacco.id),
            ).count(),
            1,
        )


class SavingsWithdrawalAuditTest(TestCase):
    def setUp(self):
        self.sacco, self.user, self.membership, self.saving = (
            _make_withdrawal_ready_sacco(
                'WAUD1', 'waud-m@example.com', '254712260001', '10000.00',
            )
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.url = reverse('payments:mpesa-b2c-withdraw')

    def _post(self, *, idempotency_key, gross='5000.00', b2c=None,
              b2c_error=None):
        with patch('payments.withdrawals.DarajaClient') as client_cls:
            client = client_cls.return_value
            client._build_callback_url.return_value = 'https://cb.test/b2c'
            if b2c_error is not None:
                client.initiate_b2c.side_effect = b2c_error
            else:
                client.initiate_b2c.return_value = b2c
            return self.client.post(
                self.url,
                {
                    'phone_number': '254712260001',
                    'amount': gross,
                    'sacco_id': str(self.sacco.id),
                    'saving_id': str(self.saving.id),
                    'idempotency_key': idempotency_key,
                },
                format='json',
            )

    def _reserve(self, conversation_id, *, gross='5000.00'):
        response = self._post(
            idempotency_key=f'aud-{conversation_id}',
            gross=gross,
            b2c={
                'ConversationID': conversation_id,
                'OriginatorConversationID': f'ORIG-{conversation_id}',
            },
        )
        self.assertEqual(response.status_code, 201)
        payment = Transaction.objects.get(external_reference=conversation_id)
        return payment, MpesaTransaction.objects.get(transaction=payment)

    def test_successful_withdrawal_audits_initiation_and_completion(self):
        payment, mpesa = self._reserve('CONV-AUD-OK')

        self.assertTrue(
            SystemAuditLog.objects.filter(
                user=self.user,
                action='SAVINGS_WITHDRAWAL_INITIATED',
                resource_id=str(self.saving.id),
                new_values__transaction_id=str(payment.id),
            ).exists()
        )

        with self.captureOnCommitCallbacks(execute=True):
            _process_successful_b2c_callback(
                mpesa, payment, _b2c_result_ok('CONV-AUD-OK'),
            )

        completed = SystemAuditLog.objects.get(
            user=self.user,
            action='SAVINGS_WITHDRAWAL_COMPLETED',
            resource_type='Saving',
            resource_id=str(self.saving.id),
        )
        self.assertEqual(
            completed.new_values['sacco_id'], str(self.sacco.id),
        )
        self.assertEqual(
            completed.new_values['transaction_id'], str(payment.id),
        )
        self.assertEqual(completed.new_values['gross_amount'], '5000.00')
        self.assertEqual(
            completed.new_values['mpesa_receipt_number'], 'QWE12345XY',
        )
        self.assertEqual(
            completed.new_values['ledger_entry_reference'], str(payment.id),
        )

    def test_failed_callback_writes_failed_audit_once(self):
        payment, mpesa = self._reserve('CONV-AUD-FAIL')

        with self.captureOnCommitCallbacks(execute=True):
            _process_failed_b2c_callback(
                mpesa, payment, _b2c_result_failed('CONV-AUD-FAIL'), 2001,
            )

        failed = SystemAuditLog.objects.get(
            user=self.user,
            action='SAVINGS_WITHDRAWAL_FAILED',
            resource_id=str(self.saving.id),
        )
        self.assertEqual(
            failed.new_values['sacco_id'], str(self.sacco.id),
        )
        self.assertEqual(
            failed.new_values['transaction_id'], str(payment.id),
        )
        self.assertEqual(
            failed.new_values['reversal_ledger_reference'],
            f'{payment.id}-REV',
        )
        self.assertEqual(failed.new_values['response_code'], '2001')
        self.assertTrue(failed.new_values['balance_re_credited'])

        # A second reversal call (sync path + async callback both firing)
        # must not write a second audit row.
        _reverse_withdrawal(payment, reason='second call', response_code=None)
        self.assertEqual(
            SystemAuditLog.objects.filter(
                action='SAVINGS_WITHDRAWAL_FAILED',
                resource_id=str(self.saving.id),
            ).count(),
            1,
        )

    def test_daraja_error_at_initiation_audits_initiated_and_failed(self):
        from payments.integrations.mpesa.daraja import DarajaError

        response = self._post(
            idempotency_key='aud-daraja-err',
            b2c_error=DarajaError(
                'The initiator information is invalid.', '2001',
            ),
        )

        self.assertEqual(response.status_code, 502)
        payment = Transaction.objects.get(
            transaction_type=Transaction.TransactionType.WITHDRAWAL,
        )
        self.assertTrue(
            SystemAuditLog.objects.filter(
                user=self.user,
                action='SAVINGS_WITHDRAWAL_INITIATED',
                new_values__transaction_id=str(payment.id),
            ).exists()
        )
        self.assertTrue(
            SystemAuditLog.objects.filter(
                user=self.user,
                action='SAVINGS_WITHDRAWAL_FAILED',
                new_values__transaction_id=str(payment.id),
                new_values__sacco_id=str(self.sacco.id),
            ).exists()
        )

    def test_withdrawal_audit_is_queryable_per_sacco(self):
        payment, mpesa = self._reserve('CONV-AUD-SCOPE')
        other_sacco, *_rest = _make_withdrawal_ready_sacco(
            'WAUD2', 'waud-o@example.com', '254712260002', '10000.00',
        )

        with self.captureOnCommitCallbacks(execute=True):
            _process_successful_b2c_callback(
                mpesa, payment, _b2c_result_ok('CONV-AUD-SCOPE'),
            )

        self.assertEqual(
            SystemAuditLog.objects.filter(
                action='SAVINGS_WITHDRAWAL_COMPLETED',
                new_values__sacco_id=str(self.sacco.id),
            ).count(),
            1,
        )
        self.assertFalse(
            SystemAuditLog.objects.filter(
                action='SAVINGS_WITHDRAWAL_COMPLETED',
                new_values__sacco_id=str(other_sacco.id),
            ).exists()
        )
