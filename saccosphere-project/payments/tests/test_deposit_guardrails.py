"""Deposit guardrails: minimum contribution + account status.

A. STK deposit below SavingsType.minimum_contribution is rejected with
   the actual minimum shown.
B. STK deposit into a FROZEN / CLOSED account is rejected before the STK
   push goes out (in _get_owned_saving).
C. The frozen-between-initiation-and-callback race:
   _apply_saving_deposit still CREDITS the money (member already paid),
   raises a compliance flag + audit event, and leaves the account
   non-active. Product decision - never lose member funds.
"""

from decimal import Decimal

from django.test import TestCase
from rest_framework import serializers
from rest_framework.test import APIClient

from accounts.models import Sacco, User
from ledger.models import LedgerEntry
from ledger.utils import apply_ledger_entry, savings_ledger_balance
from payments.models import MpesaTransaction, PaymentProvider, Transaction
from payments.tasks import _apply_saving_deposit
from payments.views import STKPushView
from saccomanagement.models import ComplianceFlag, SystemAuditLog
from saccomembership.models import Membership
from services.models import Saving, SavingsType


class _DepositFixture(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            email='dep-guard@example.com',
            phone_number='254712900123',
            password='StrongPass1',
        )
        self.sacco = Sacco.objects.create(
            name='Deposit Guard SACCO',
            registration_number='DEPG-001',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
            membership_type=Sacco.MembershipType.OPEN,
        )
        self.membership = Membership.objects.create(
            user=self.user,
            sacco=self.sacco,
            status=Membership.Status.APPROVED,
            member_number='DEPG-M-001',
        )
        self.savings_type = SavingsType.objects.create(
            sacco=self.sacco,
            name=SavingsType.Name.BOSA,
            minimum_contribution=Decimal('500.00'),
        )
        self.saving = Saving.objects.create(
            membership=self.membership,
            savings_type=self.savings_type,
            amount=Decimal('0.00'),
            status=Saving.Status.ACTIVE,
        )
        # Seed the opening balance through the ledger so reconciliation
        # is green before the tests do anything.
        apply_ledger_entry(
            saving=self.saving,
            amount=Decimal('1000.00'),
            entry_type=LedgerEntry.EntryType.CREDIT,
            category=LedgerEntry.Category.SAVING_DEPOSIT,
            description='seed deposit',
            reference='DEPG-SEED',
            contribution_delta=Decimal('1000.00'),
        )
        self.saving.refresh_from_db()
        self.provider = PaymentProvider.objects.create(
            name='M-Pesa',
            provider_type=PaymentProvider.ProviderType.MPESA,
            is_active=True,
        )

    def _stk_payload(self, amount):
        return {
            'phone_number': '254712900123',
            'amount': str(amount),
            'purpose': 'SAVING_DEPOSIT',
            'sacco_id': str(self.sacco.id),
            'saving_id': str(self.saving.id),
        }

    def _post(self, amount):
        self.client.force_authenticate(user=self.user)
        return self.client.post(
            '/api/v1/payments/mpesa/stk-push/',
            self._stk_payload(amount),
            format='json',
        )

    def _resolve_target(self, amount):
        """Call the real guardrail (STKPushView._get_owned_saving)."""
        return STKPushView()._get_owned_saving(
            self.user,
            {
                'saving_id': str(self.saving.id),
                'sacco_id': str(self.sacco.id),
                'amount': Decimal(amount),
            },
        )


class MinimumContributionTests(_DepositFixture):
    def test_deposit_below_minimum_is_rejected_with_the_actual_minimum(self):
        response = self._post(Decimal('250.00'))

        self.assertEqual(response.status_code, 400)
        body = str(response.json())
        self.assertIn('500.00', body)
        self.assertIn('minimum contribution', body.lower())
        self.assertFalse(Transaction.objects.exists())

    def test_deposit_at_the_minimum_passes_the_guardrail(self):
        saving = self._resolve_target(Decimal('500.00'))
        self.assertEqual(saving.pk, self.saving.pk)

    def test_unconfigured_minimum_does_not_block_a_small_deposit(self):
        self.savings_type.minimum_contribution = Decimal('0.00')
        self.savings_type.save(update_fields=['minimum_contribution'])

        saving = self._resolve_target(Decimal('20.00'))
        self.assertEqual(saving.pk, self.saving.pk)


class DepositAccountStatusTests(_DepositFixture):
    def test_deposit_into_frozen_account_is_rejected_pre_callback(self):
        self.saving.status = Saving.Status.FROZEN
        self.saving.save(update_fields=['status'])

        response = self._post(Decimal('600.00'))

        self.assertEqual(response.status_code, 400)
        self.assertIn('frozen', str(response.json()).lower())
        self.assertFalse(Transaction.objects.exists())

    def test_deposit_into_closed_account_is_rejected_pre_callback(self):
        self.saving.status = Saving.Status.CLOSED
        self.saving.save(update_fields=['status'])

        with self.assertRaises(serializers.ValidationError) as ctx:
            self._resolve_target(Decimal('600.00'))
        self.assertIn('closed', str(ctx.exception.detail).lower())

    def test_active_account_passes_the_guardrail(self):
        saving = self._resolve_target(Decimal('600.00'))
        self.assertEqual(saving.pk, self.saving.pk)


class FrozenBetweenInitiationAndCallbackTests(_DepositFixture):
    """Product decision: credit with a flag; never lose member funds."""

    def _transaction(self, amount):
        return Transaction.objects.create(
            provider=self.provider,
            user=self.user,
            reference='DEPG-RACE-1',
            transaction_type=Transaction.TransactionType.DEPOSIT,
            amount=amount,
            sacco=self.sacco,
            status=Transaction.Status.COMPLETED,
            description='Race test',
        )

    def _mpesa(self, transaction):
        return MpesaTransaction.objects.create(
            transaction=transaction,
            phone_number='254712900123',
            checkout_request_id='CR-DEPG-RACE-1',
            mpesa_receipt_number='RCT-DEPG-RACE-1',
            related_saving=self.saving,
        )

    def test_callback_for_now_frozen_account_credits_and_flags(self):
        # Initiated ACTIVE, frozen before the callback arrives.
        self.saving.status = Saving.Status.FROZEN
        self.saving.save(update_fields=['status'])
        transaction = self._transaction(Decimal('600.00'))
        mpesa_transaction = self._mpesa(transaction)
        before = self.saving.amount

        _apply_saving_deposit(
            mpesa_transaction, transaction, Decimal('600.00'),
        )

        self.saving.refresh_from_db()
        # Member is made whole - the money is credited.
        self.assertEqual(self.saving.amount, before + Decimal('600.00'))
        # ...but the account is NOT reactivated.
        self.assertEqual(self.saving.status, Saving.Status.FROZEN)

        entry = LedgerEntry.objects.get(reference=str(transaction.id))
        self.assertEqual(entry.entry_type, LedgerEntry.EntryType.CREDIT)
        self.assertEqual(
            entry.category, LedgerEntry.Category.SAVING_DEPOSIT,
        )
        self.assertIn('PENDING COMPLIANCE REVIEW', entry.description)
        # Reconciliation stays green (savings-category ledger row).
        self.assertEqual(
            savings_ledger_balance(self.membership), self.saving.amount,
        )

        flag = ComplianceFlag.objects.get(
            sacco=self.sacco,
            flag_type=ComplianceFlag.FlagType.PAYMENT_FAILURE,
        )
        self.assertEqual(flag.severity, ComplianceFlag.Severity.HIGH)
        self.assertEqual(
            flag.metadata['account_status'], Saving.Status.FROZEN,
        )
        self.assertEqual(flag.metadata['saving_id'], str(self.saving.id))
        self.assertEqual(flag.metadata['amount'], '600.00')

        audit = SystemAuditLog.objects.get(
            action='DEPOSIT_INTO_INACTIVE_ACCOUNT',
            resource_type='Saving',
            resource_id=str(self.saving.id),
        )
        self.assertEqual(
            audit.new_values['resolution'], 'credited_pending_review',
        )
        self.assertEqual(
            audit.new_values['account_status'], Saving.Status.FROZEN,
        )

    def test_callback_for_active_account_does_not_flag(self):
        transaction = self._transaction(Decimal('600.00'))
        mpesa_transaction = self._mpesa(transaction)

        _apply_saving_deposit(
            mpesa_transaction, transaction, Decimal('600.00'),
        )

        self.saving.refresh_from_db()
        self.assertEqual(self.saving.amount, Decimal('1600.00'))
        entry = LedgerEntry.objects.get(reference=str(transaction.id))
        self.assertNotIn('PENDING COMPLIANCE REVIEW', entry.description)
        self.assertFalse(
            ComplianceFlag.objects.filter(
                flag_type=ComplianceFlag.FlagType.PAYMENT_FAILURE,
            ).exists()
        )
        self.assertFalse(
            SystemAuditLog.objects.filter(
                action='DEPOSIT_INTO_INACTIVE_ACCOUNT',
            ).exists()
        )
