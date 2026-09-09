"""Repayment/ledger correctness: gross balance, penalties, OVERDUE sweep."""

from datetime import timedelta
from decimal import Decimal

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from accounts.models import Sacco, SaccoSettings, User
from ledger.models import LedgerEntry
from notifications.models import Notification
from payments.models import (
    MpesaTransaction,
    PaymentProvider,
    Transaction,
)
from payments.tasks import _process_successful_b2c_callback
from saccomanagement.loan_utils import persist_loan_repayment_schedule
from saccomembership.models import Membership
from services.engines.penalties import compute_penalty
from services.models import Loan, LoanType, ReminderLog, RepaymentSchedule
from services.tasks import mark_overdue_instalments


class _LoanFixtureMixin:
    def setUp(self):
        self.user = User.objects.create_user(
            email='repay-correctness@example.com',
            phone_number='254700005001',
            password='StrongPass1',
        )
        self.sacco = Sacco.objects.create(
            name='Repay SACCO',
            registration_number='REPAY-001',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
            membership_type=Sacco.MembershipType.OPEN,
        )
        self.membership = Membership.objects.create(
            user=self.user,
            sacco=self.sacco,
            status=Membership.Status.APPROVED,
            member_number='REPAY-M-001',
            approved_date=timezone.now(),
        )
        self.loan_type = LoanType.objects.create(
            sacco=self.sacco,
            name='Repay Loan',
            interest_rate=Decimal('12.00'),
            max_term_months=24,
            min_amount=Decimal('1000.00'),
        )
        self.loan = Loan.objects.create(
            membership=self.membership,
            loan_type=self.loan_type,
            amount=Decimal('120000.00'),
            interest_rate=Decimal('12.00'),
            term_months=12,
            outstanding_balance=Decimal('0.00'),
            status=Loan.Status.APPROVED,
        )


class GrossNetReconciliationTest(_LoanFixtureMixin, TestCase):
    """Item 1: the schedule and outstanding_balance both track the gross."""

    def test_schedule_principal_equals_gross_and_balance(self):
        persist_loan_repayment_schedule(self.loan)
        self.loan.refresh_from_db()

        principal_total = sum(
            (row.principal for row in self.loan.schedule.all()),
            Decimal('0.00'),
        )
        self.assertEqual(principal_total, self.loan.amount)
        self.assertEqual(self.loan.outstanding_balance, self.loan.amount)

    def test_disbursement_callback_sets_gross_balance_not_net(self):
        persist_loan_repayment_schedule(self.loan)

        provider = PaymentProvider.objects.create(
            name='M-Pesa',
            provider_type=PaymentProvider.ProviderType.MPESA,
            is_active=True,
        )
        gross = self.loan.amount
        net = gross - Decimal('250.00')  # platform fee withheld
        payment = Transaction.objects.create(
            provider=provider,
            sacco=self.sacco,
            user=self.user,
            reference='REPAY-DISB-001',
            transaction_type=Transaction.TransactionType.LOAN_DISBURSEMENT,
            amount=net,
            gross_amount=gross,
            platform_fee=Decimal('250.00'),
            status=Transaction.Status.SENT,
            description='disbursement',
        )
        mpesa = MpesaTransaction.objects.create(
            transaction=payment,
            phone_number='254700005001',
            conversation_id='CONV-REPAY-001',
            transaction_type=MpesaTransaction.TransactionType.B2C,
            related_loan=self.loan,
        )
        result = {
            'ResultCode': 0,
            'ResultDesc': 'Success',
            'ResultParameters': {
                'ResultParameter': [
                    {'Key': 'TransactionReceipt', 'Value': 'RCPT-1'},
                ],
            },
        }

        _process_successful_b2c_callback(mpesa, payment, result)

        self.loan.refresh_from_db()
        principal_total = sum(
            (row.principal for row in self.loan.schedule.all()),
            Decimal('0.00'),
        )
        self.assertEqual(self.loan.outstanding_balance, gross)
        self.assertEqual(self.loan.outstanding_balance, principal_total)
        # disbursed_amount is what actually reached M-Pesa (net).
        self.assertEqual(self.loan.disbursed_amount, net)
        ledger = LedgerEntry.objects.get(
            reference=f'{payment.reference}-LEDGER',
        )
        self.assertEqual(ledger.amount, gross)


class PenaltyComputationTest(_LoanFixtureMixin, TestCase):
    """Item 3: compute_penalty against the per-SACCO configurable rule."""

    def _instalment(self, due_offset_days, amount='10000.00', paid=None):
        return RepaymentSchedule.objects.create(
            loan=self.loan,
            instalment_number=RepaymentSchedule.objects.filter(
                loan=self.loan,
            ).count() + 1,
            due_date=timezone.localdate() + timedelta(days=due_offset_days),
            amount=Decimal(amount),
            principal=Decimal('9000.00'),
            interest=Decimal('1000.00'),
            balance_after=Decimal('0.00'),
            paid_amount=Decimal(paid) if paid is not None else None,
        )

    def _settings(self, **kwargs):
        return SaccoSettings.objects.create(sacco=self.sacco, **kwargs)

    def test_none_type_or_missing_settings_is_zero(self):
        item = self._instalment(-10)
        self.assertEqual(compute_penalty(item, None), Decimal('0.00'))
        self.assertEqual(
            compute_penalty(item, self._settings()), Decimal('0.00'),
        )

    def test_flat_fee_once_overdue(self):
        settings_obj = self._settings(
            penalty_type=SaccoSettings.PenaltyType.FLAT,
            penalty_rate=Decimal('500.0000'),
        )
        self.assertEqual(
            compute_penalty(self._instalment(-1), settings_obj),
            Decimal('500.00'),
        )
        # Not yet due -> nothing.
        self.assertEqual(
            compute_penalty(self._instalment(3), settings_obj),
            Decimal('0.00'),
        )

    def test_percent_of_instalment_once(self):
        settings_obj = self._settings(
            penalty_type=SaccoSettings.PenaltyType.PERCENT_ONCE,
            penalty_rate=Decimal('0.0500'),
        )
        # 5% of 10,000, regardless of how many days late.
        self.assertEqual(
            compute_penalty(self._instalment(-30), settings_obj),
            Decimal('500.00'),
        )

    def test_percent_per_day_accrues_and_respects_grace(self):
        settings_obj = self._settings(
            penalty_type=SaccoSettings.PenaltyType.PERCENT_PER_DAY,
            penalty_rate=Decimal('0.0100'),
            penalty_grace_days=3,
        )
        # 10 days late, 3 grace -> 7 chargeable days x 1% x 10,000 = 700.
        self.assertEqual(
            compute_penalty(self._instalment(-10), settings_obj),
            Decimal('700.00'),
        )
        # Within grace -> zero.
        self.assertEqual(
            compute_penalty(self._instalment(-2), settings_obj),
            Decimal('0.00'),
        )

    def test_fully_paid_instalment_has_no_penalty(self):
        settings_obj = self._settings(
            penalty_type=SaccoSettings.PenaltyType.FLAT,
            penalty_rate=Decimal('500.0000'),
        )
        item = self._instalment(-10, paid='10000.00')
        self.assertEqual(compute_penalty(item, settings_obj), Decimal('0.00'))


class OverdueSweepAndReminderTest(_LoanFixtureMixin, TestCase):
    """Items 3+4: OVERDUE gets set, penalty accrues, overdue alert fires."""

    def setUp(self):
        super().setUp()
        self.loan.status = Loan.Status.ACTIVE
        self.loan.outstanding_balance = self.loan.amount
        self.loan.save(
            update_fields=['status', 'outstanding_balance', 'updated_at'],
        )
        SaccoSettings.objects.create(
            sacco=self.sacco,
            penalty_type=SaccoSettings.PenaltyType.PERCENT_ONCE,
            penalty_rate=Decimal('0.1000'),
        )
        self.yesterday = timezone.localdate() - timedelta(days=1)
        self.item = RepaymentSchedule.objects.create(
            loan=self.loan,
            instalment_number=1,
            due_date=self.yesterday,
            amount=Decimal('10000.00'),
            principal=Decimal('9000.00'),
            interest=Decimal('1000.00'),
            balance_after=Decimal('110000.00'),
            status=RepaymentSchedule.Status.PENDING,
        )
        # A future instalment must be left untouched.
        self.future_item = RepaymentSchedule.objects.create(
            loan=self.loan,
            instalment_number=2,
            due_date=timezone.localdate() + timedelta(days=29),
            amount=Decimal('10000.00'),
            principal=Decimal('9000.00'),
            interest=Decimal('1000.00'),
            balance_after=Decimal('100000.00'),
            status=RepaymentSchedule.Status.PENDING,
        )

    def test_sweep_flips_overdue_and_sets_penalty(self):
        result = mark_overdue_instalments()

        self.item.refresh_from_db()
        self.future_item.refresh_from_db()
        self.assertEqual(result['flipped'], 1)
        self.assertEqual(
            self.item.status, RepaymentSchedule.Status.OVERDUE,
        )
        self.assertEqual(self.item.penalty_amount, Decimal('1000.00'))
        self.assertEqual(
            self.future_item.status, RepaymentSchedule.Status.PENDING,
        )

    def test_reminders_command_fires_overdue_alert_after_sweep(self):
        mark_overdue_instalments()

        call_command('send_repayment_reminders')

        self.assertTrue(
            ReminderLog.objects.filter(
                schedule_item=self.item,
                reminder_type=ReminderLog.ReminderType.OVERDUE,
            ).exists()
        )
        notice = Notification.objects.get(
            user=self.user,
            title='Loan Repayment Overdue',
        )
        self.assertIn('1,000', notice.message)  # penalty in the copy

    def test_overdue_alert_is_dead_without_the_sweep(self):
        # Nothing flips the status, so the command has nothing to alert on.
        call_command('send_repayment_reminders')
        self.assertFalse(
            ReminderLog.objects.filter(
                reminder_type=ReminderLog.ReminderType.OVERDUE,
            ).exists()
        )
