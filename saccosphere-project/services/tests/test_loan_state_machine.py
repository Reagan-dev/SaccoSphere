"""Phase 7: loan state-machine closure.

Covers:
  * full repayment -> COMPLETED and guarantor capacity release
    (payments.tasks._complete_loan_if_fully_repaid + the
    refresh_capacity_on_loan_completion signal);
  * BOARD_REVIEW removed - the guarantors-complete gate lands on
    PENDING_APPROVAL, never a board state;
  * DEFAULTED flips at 90 days overdue and auto-recovers when arrears
    clear (services.tasks.flag_npl_arrears);
  * the unified guarantor gate (count AND coverage) behaves identically
    at the entry gate (GuarantorRespondView) and the final admin gate.
"""

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import Sacco, User
from payments.models import MpesaTransaction, PaymentProvider, Transaction
from payments.tasks import _apply_loan_repayment
from saccomanagement.models import Role
from saccomembership.models import Membership
from services.engines.loan_limits import calculate_loan_limit
from services.models import (
    CRBCheck,
    GuaranteeCapacity,
    Guarantor,
    Loan,
    LoanType,
    NPLFlag,
    RepaymentSchedule,
    Saving,
    SavingsType,
)
from services.tasks import flag_npl_arrears


class _BaseFixture(TestCase):
    def setUp(self):
        self.sacco = Sacco.objects.create(
            name='State Machine SACCO',
            registration_number='SM-001',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
        )
        self.provider = PaymentProvider.objects.create(
            name='M-Pesa',
            provider_type=PaymentProvider.ProviderType.MPESA,
            is_active=True,
        )
        self.savings_type = SavingsType.objects.create(
            sacco=self.sacco,
            name=SavingsType.Name.BOSA,
            minimum_contribution=Decimal('100.00'),
        )
        self.borrower = User.objects.create_user(
            email='sm-borrower@example.com',
            phone_number='254700009001',
            password='StrongPass1',
            first_name='Bo',
            last_name='Rrower',
        )
        self.membership = Membership.objects.create(
            user=self.borrower,
            sacco=self.sacco,
            status=Membership.Status.APPROVED,
            member_number='SM-M-001',
            approved_date=timezone.now(),
        )
        self.loan_type = LoanType.objects.create(
            sacco=self.sacco,
            name='SM Loan',
            interest_rate=Decimal('12.00'),
            max_term_months=24,
            min_amount=Decimal('100.00'),
            requires_guarantors=True,
            min_guarantors=2,
        )

    def _guarantor_user(self, suffix, savings):
        user = User.objects.create_user(
            email=f'sm-guar-{suffix}@example.com',
            phone_number=f'25470000{suffix:04d}',
            password='StrongPass1',
        )
        membership = Membership.objects.create(
            user=user,
            sacco=self.sacco,
            status=Membership.Status.APPROVED,
            member_number=f'SM-G-{suffix:03d}',
            approved_date=timezone.now(),
        )
        Saving.objects.create(
            membership=membership,
            savings_type=self.savings_type,
            amount=savings,
            status=Saving.Status.ACTIVE,
        )
        GuaranteeCapacity.objects.create(
            user=user,
            total_savings=savings,
            active_guarantees=Decimal('0.00'),
            available_capacity=savings * Decimal('0.50'),
        )
        return user


class FullRepaymentCompletesLoanTest(_BaseFixture):
    """(a) full repayment flips ACTIVE -> COMPLETED and frees capacity."""

    def setUp(self):
        super().setUp()
        self.loan = Loan.objects.create(
            membership=self.membership,
            loan_type=self.loan_type,
            amount=Decimal('300.00'),
            interest_rate=Decimal('12.00'),
            term_months=3,
            outstanding_balance=Decimal('300.00'),
            status=Loan.Status.ACTIVE,
        )
        due = timezone.localdate() + timedelta(days=30)
        for number in range(1, 4):
            RepaymentSchedule.objects.create(
                loan=self.loan,
                instalment_number=number,
                due_date=due + timedelta(days=30 * (number - 1)),
                amount=Decimal('100.00'),
                principal=Decimal('90.00'),
                interest=Decimal('10.00'),
                balance_after=Decimal('300.00') - Decimal('100.00') * number,
            )
        self.guarantor_user = self._guarantor_user(1, Decimal('10000.00'))
        self.guarantee = Guarantor.objects.create(
            loan=self.loan,
            guarantor=self.guarantor_user,
            guarantee_amount=Decimal('300.00'),
            status=Guarantor.Status.APPROVED,
        )
        # Reserve the capacity the way an approval would have.
        GuaranteeCapacity.objects.filter(user=self.guarantor_user).update(
            active_guarantees=Decimal('300.00'),
            available_capacity=Decimal('4700.00'),
        )

    def _repay(self, amount, reference, instalment_number=1):
        transaction = Transaction.objects.create(
            provider=self.provider,
            user=self.borrower,
            reference=reference,
            transaction_type=Transaction.TransactionType.LOAN_REPAYMENT,
            amount=amount,
            sacco=self.sacco,
            status=Transaction.Status.PENDING,
            description='SM repayment',
        )
        mpesa = MpesaTransaction.objects.create(
            transaction=transaction,
            phone_number='254700009001',
            checkout_request_id=f'CHK-{reference}',
            related_loan=self.loan,
            related_instalment_number=instalment_number,
        )
        _apply_loan_repayment(mpesa, transaction, amount)

    def test_partial_repayment_leaves_loan_active(self):
        self._repay(Decimal('100.00'), 'SM-REP-1')
        self.loan.refresh_from_db()
        self.assertEqual(self.loan.status, Loan.Status.ACTIVE)

    def test_final_repayment_completes_loan_and_releases_capacity(self):
        self._repay(Decimal('300.00'), 'SM-REP-FULL')

        self.loan.refresh_from_db()
        self.assertEqual(self.loan.status, Loan.Status.COMPLETED)
        self.assertEqual(self.loan.outstanding_balance, Decimal('0.00'))
        self.assertFalse(
            RepaymentSchedule.objects.filter(
                loan=self.loan,
            ).exclude(status=RepaymentSchedule.Status.PAID).exists()
        )

        capacity = GuaranteeCapacity.objects.get(user=self.guarantor_user)
        self.assertEqual(capacity.active_guarantees, Decimal('0.00'))
        self.assertEqual(capacity.available_capacity, Decimal('5000.00'))

    def test_completion_is_idempotent_on_a_late_extra_payment(self):
        self._repay(Decimal('300.00'), 'SM-REP-FULL')
        self.loan.refresh_from_db()
        self.assertEqual(self.loan.status, Loan.Status.COMPLETED)

        # A stray extra payment must not crash or un-complete the loan;
        # it books an overpayment liability instead.
        self._repay(Decimal('50.00'), 'SM-REP-EXTRA')
        self.loan.refresh_from_db()
        self.assertEqual(self.loan.status, Loan.Status.COMPLETED)


class BoardReviewRemovedTest(_BaseFixture):
    """(b) BOARD_REVIEW is gone; guarantors-complete -> PENDING_APPROVAL."""

    def setUp(self):
        super().setUp()
        self.loan = Loan.objects.create(
            membership=self.membership,
            loan_type=self.loan_type,
            amount=Decimal('30000.00'),
            interest_rate=Decimal('12.00'),
            term_months=12,
            outstanding_balance=Decimal('30000.00'),
            status=Loan.Status.GUARANTORS_PENDING,
        )
        self.g1 = self._guarantor_user(11, Decimal('80000.00'))
        self.g2 = self._guarantor_user(12, Decimal('80000.00'))
        self.req1 = Guarantor.objects.create(
            loan=self.loan,
            guarantor=self.g1,
            guarantee_amount=Decimal('20000.00'),
            status=Guarantor.Status.PENDING,
        )
        self.req2 = Guarantor.objects.create(
            loan=self.loan,
            guarantor=self.g2,
            guarantee_amount=Decimal('20000.00'),
            status=Guarantor.Status.PENDING,
        )

    def test_status_enum_no_longer_has_board_review(self):
        self.assertFalse(hasattr(Loan.Status, 'BOARD_REVIEW'))
        self.assertNotIn(
            'BOARD_REVIEW',
            {choice[0] for choice in Loan.Status.choices},
        )

    def _respond(self, user, guarantor_req, action='APPROVE'):
        client = APIClient()
        client.force_authenticate(user=user)
        return client.post(
            reverse(
                'services:guarantor-respond',
                kwargs={
                    'loan_id': self.loan.id,
                    'guarantor_id': guarantor_req.id,
                },
            ),
            {'action': action},
            format='json',
        )

    def test_guarantors_complete_lands_on_pending_approval(self):
        self._respond(self.g1, self.req1)
        self.loan.refresh_from_db()
        self.assertEqual(self.loan.status, Loan.Status.GUARANTORS_PENDING)

        self._respond(self.g2, self.req2)
        self.loan.refresh_from_db()
        self.assertEqual(self.loan.status, Loan.Status.PENDING_APPROVAL)


class DefaultedStatusTransitionTest(_BaseFixture):
    """(c) DEFAULTED at 90 days overdue, with auto-recovery."""

    def setUp(self):
        super().setUp()
        # Age the membership so calculate_loan_limit reaches the
        # has-default gate rather than short-circuiting on tenure.
        self.membership.approved_date = timezone.now() - timedelta(days=400)
        self.membership.save(update_fields=['approved_date'])
        Saving.objects.create(
            membership=self.membership,
            savings_type=self.savings_type,
            amount=Decimal('50000.00'),
            status=Saving.Status.ACTIVE,
        )
        self.loan = Loan.objects.create(
            membership=self.membership,
            loan_type=self.loan_type,
            amount=Decimal('20000.00'),
            interest_rate=Decimal('12.00'),
            term_months=12,
            outstanding_balance=Decimal('20000.00'),
            status=Loan.Status.ACTIVE,
        )
        self.instalment = RepaymentSchedule.objects.create(
            loan=self.loan,
            instalment_number=1,
            due_date=timezone.localdate() - timedelta(days=95),
            amount=Decimal('1800.00'),
            principal=Decimal('1600.00'),
            interest=Decimal('200.00'),
            balance_after=Decimal('18400.00'),
            status=RepaymentSchedule.Status.PENDING,
        )

    @patch('services.tasks.send_sms_notification')
    def test_flip_to_defaulted_at_90_days_and_block_borrowing(self, _sms):
        result = flag_npl_arrears()

        self.assertEqual(result['loans_defaulted'], 1)
        self.loan.refresh_from_db()
        self.assertEqual(self.loan.status, Loan.Status.DEFAULTED)
        self.assertTrue(
            NPLFlag.objects.filter(
                loan=self.loan,
                threshold_days=NPLFlag.ThresholdDays.NINETY,
            ).exists()
        )

        limit = calculate_loan_limit(self.borrower, self.sacco)
        self.assertFalse(limit['eligible'])
        self.assertEqual(limit['reason'], 'HAS_DEFAULT')

    @patch('services.tasks.send_sms_notification')
    def test_defaulted_loan_auto_recovers_when_arrears_clear(self, _sms):
        flag_npl_arrears()
        self.loan.refresh_from_db()
        self.assertEqual(self.loan.status, Loan.Status.DEFAULTED)

        self.instalment.status = RepaymentSchedule.Status.PAID
        self.instalment.paid_amount = self.instalment.amount
        self.instalment.paid_date = timezone.localdate()
        self.instalment.save(
            update_fields=['status', 'paid_amount', 'paid_date'],
        )

        result = flag_npl_arrears()
        self.assertEqual(result['loans_recovered'], 1)
        self.loan.refresh_from_db()
        self.assertEqual(self.loan.status, Loan.Status.ACTIVE)


class UnifiedGuarantorGateTest(_BaseFixture):
    """(d) count AND coverage enforced at BOTH gates."""

    def setUp(self):
        super().setUp()
        self.admin = User.objects.create_user(
            email='sm-admin@example.com',
            phone_number='254700009900',
            password='StrongPass1',
        )
        Role.objects.create(
            user=self.admin,
            sacco=self.sacco,
            name=Role.SACCO_ADMIN,
        )

    def _make_loan(self, status):
        return Loan.objects.create(
            membership=self.membership,
            loan_type=self.loan_type,
            amount=Decimal('30000.00'),
            interest_rate=Decimal('12.00'),
            term_months=12,
            outstanding_balance=Decimal('30000.00'),
            status=status,
        )

    # --- entry gate: GuarantorRespondView ---------------------------------

    def test_entry_gate_blocks_when_count_met_but_coverage_short(self):
        loan = self._make_loan(Loan.Status.GUARANTORS_PENDING)
        loan.loan_type.min_guarantors = 1
        loan.loan_type.save(update_fields=['min_guarantors'])
        guar = self._guarantor_user(21, Decimal('80000.00'))
        req = Guarantor.objects.create(
            loan=loan,
            guarantor=guar,
            guarantee_amount=Decimal('10000.00'),
            status=Guarantor.Status.PENDING,
        )

        client = APIClient()
        client.force_authenticate(user=guar)
        response = client.post(
            reverse(
                'services:guarantor-respond',
                kwargs={'loan_id': loan.id, 'guarantor_id': req.id},
            ),
            {'action': 'APPROVE'},
            format='json',
        )

        self.assertEqual(response.status_code, 200)
        loan.refresh_from_db()
        # Count satisfied (1/1) but 10k < 30k coverage -> not advanced.
        self.assertEqual(loan.status, Loan.Status.GUARANTORS_PENDING)

    def test_entry_gate_advances_when_count_and_coverage_met(self):
        loan = self._make_loan(Loan.Status.GUARANTORS_PENDING)
        g1 = self._guarantor_user(22, Decimal('80000.00'))
        g2 = self._guarantor_user(23, Decimal('80000.00'))
        req1 = Guarantor.objects.create(
            loan=loan, guarantor=g1,
            guarantee_amount=Decimal('16000.00'),
            status=Guarantor.Status.PENDING,
        )
        req2 = Guarantor.objects.create(
            loan=loan, guarantor=g2,
            guarantee_amount=Decimal('16000.00'),
            status=Guarantor.Status.PENDING,
        )
        for user, req in ((g1, req1), (g2, req2)):
            client = APIClient()
            client.force_authenticate(user=user)
            client.post(
                reverse(
                    'services:guarantor-respond',
                    kwargs={'loan_id': loan.id, 'guarantor_id': req.id},
                ),
                {'action': 'APPROVE'},
                format='json',
            )

        loan.refresh_from_db()
        self.assertEqual(loan.status, Loan.Status.PENDING_APPROVAL)

    # --- final gate: admin loan approval --------------------------------

    def _approve(self, loan):
        client = APIClient()
        client.force_authenticate(user=self.admin)
        return client.patch(
            reverse('management:loan-approval', kwargs={'id': loan.id}),
            {'status': Loan.Status.APPROVED},
            format='json',
        )

    def test_final_gate_blocks_when_coverage_met_but_count_short(self):
        loan = self._make_loan(Loan.Status.UNDER_REVIEW)
        CRBCheck.objects.create(loan=loan, listed_negative=False)
        solo = self._guarantor_user(31, Decimal('120000.00'))
        Guarantor.objects.create(
            loan=loan, guarantor=solo,
            guarantee_amount=Decimal('30000.00'),
            status=Guarantor.Status.APPROVED,
        )

        response = self._approve(loan)

        self.assertEqual(response.status_code, 400)
        loan.refresh_from_db()
        self.assertEqual(loan.status, Loan.Status.UNDER_REVIEW)

    def test_final_gate_approves_when_count_and_coverage_met(self):
        loan = self._make_loan(Loan.Status.UNDER_REVIEW)
        CRBCheck.objects.create(loan=loan, listed_negative=False)
        for suffix in (32, 33):
            guar = self._guarantor_user(suffix, Decimal('120000.00'))
            Guarantor.objects.create(
                loan=loan, guarantor=guar,
                guarantee_amount=Decimal('16000.00'),
                status=Guarantor.Status.APPROVED,
            )

        response = self._approve(loan)

        self.assertEqual(response.status_code, 200)
        loan.refresh_from_db()
        self.assertEqual(loan.status, Loan.Status.APPROVED)
