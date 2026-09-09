"""Tests for guarantor capacity calculation and signal-driven updates."""

from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from accounts.models import Sacco, User
from saccomembership.models import Membership
from services.engines.guarantor_logic import (
    calculate_guarantee_capacity,
    update_guarantee_capacity,
)
from services.models import Guarantor, Loan, LoanType, Saving, SavingsType


class GuarantorLogicTestCase(TestCase):
    """Validate guarantor capacity rules and automatic recalculation flow."""

    def setUp(self):
        """Create a baseline user, SACCO context, and loan type fixtures."""
        self.user = User.objects.create_user(
            email='guarantor.logic@example.com',
            first_name='Capacity',
            last_name='Tester',
            phone_number='254700000111',
            password='testpass123',
        )
        self.borrower = User.objects.create_user(
            email='borrower.logic@example.com',
            first_name='Loan',
            last_name='Borrower',
            phone_number='254700000222',
            password='testpass123',
        )
        self.sacco = Sacco.objects.create(
            name='Capacity SACCO',
            registration_number='CAP001',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
            membership_type=Sacco.MembershipType.OPEN,
        )
        self.guarantor_membership = Membership.objects.create(
            user=self.user,
            sacco=self.sacco,
            status=Membership.Status.APPROVED,
            member_number='CAP-G-001',
            approved_date=timezone.now(),
        )
        self.borrower_membership = Membership.objects.create(
            user=self.borrower,
            sacco=self.sacco,
            status=Membership.Status.APPROVED,
            member_number='CAP-B-001',
            approved_date=timezone.now(),
        )
        self.savings_type = SavingsType.objects.create(
            sacco=self.sacco,
            name=SavingsType.Name.BOSA,
            minimum_contribution=Decimal('100.00'),
        )
        self.loan_type = LoanType.objects.create(
            sacco=self.sacco,
            name='Capacity Loan',
            interest_rate=Decimal('12.00'),
            max_term_months=36,
            min_amount=Decimal('1000.00'),
            requires_guarantors=True,
            min_guarantors=1,
        )

    def _create_loan(self, status):
        """Create a borrower loan with the given status."""
        return Loan.objects.create(
            membership=self.borrower_membership,
            loan_type=self.loan_type,
            amount=Decimal('20000.00'),
            interest_rate=Decimal('12.00'),
            term_months=12,
            outstanding_balance=Decimal('20000.00'),
            status=status,
        )

    def test_50_percent_savings_rule(self):
        """Savings of 20000 should produce maximum capacity of 10000."""
        Saving.objects.create(
            membership=self.guarantor_membership,
            savings_type=self.savings_type,
            amount=Decimal('20000.00'),
            status=Saving.Status.ACTIVE,
        )

        data = calculate_guarantee_capacity(self.user)

        self.assertEqual(data['total_savings'], Decimal('20000.00'))
        self.assertEqual(data['max_guarantee_capacity'], Decimal('10000.0000'))
        self.assertEqual(data['active_guarantees'], Decimal('0'))
        self.assertEqual(data['available_capacity'], Decimal('10000.0000'))

    def test_active_guarantees_reduce_capacity(self):
        """Approved active guarantees should reduce available capacity."""
        Saving.objects.create(
            membership=self.guarantor_membership,
            savings_type=self.savings_type,
            amount=Decimal('20000.00'),
            status=Saving.Status.ACTIVE,
        )
        active_loan = self._create_loan(Loan.Status.ACTIVE)
        Guarantor.objects.create(
            loan=active_loan,
            guarantor=self.user,
            status=Guarantor.Status.APPROVED,
            guarantee_amount=Decimal('3000.00'),
        )

        data = calculate_guarantee_capacity(self.user)

        self.assertEqual(data['max_guarantee_capacity'], Decimal('10000.0000'))
        self.assertEqual(data['active_guarantees'], Decimal('3000'))
        self.assertEqual(data['available_capacity'], Decimal('7000.0000'))

    def test_loan_completion_restores_capacity(self):
        """Marking a loan completed should restore the guarantor capacity."""
        Saving.objects.create(
            membership=self.guarantor_membership,
            savings_type=self.savings_type,
            amount=Decimal('20000.00'),
            status=Saving.Status.ACTIVE,
        )
        active_loan = self._create_loan(Loan.Status.ACTIVE)
        Guarantor.objects.create(
            loan=active_loan,
            guarantor=self.user,
            status=Guarantor.Status.APPROVED,
            guarantee_amount=Decimal('3000.00'),
        )

        capacity = update_guarantee_capacity(self.user)
        self.assertEqual(capacity.available_capacity, Decimal('7000.00'))

        active_loan.status = Loan.Status.COMPLETED
        active_loan.save(update_fields=['status', 'updated_at'])

        capacity.refresh_from_db()
        self.assertEqual(capacity.active_guarantees, Decimal('0.00'))
        self.assertEqual(capacity.available_capacity, Decimal('10000.00'))

    def test_savings_are_summed_across_every_sacco(self):
        """0.5x ACTIVE savings summed across ALL of the user's SACCOs."""
        Saving.objects.create(
            membership=self.guarantor_membership,
            savings_type=self.savings_type,
            amount=Decimal('20000.00'),
            status=Saving.Status.ACTIVE,
        )
        other_sacco = Sacco.objects.create(
            name='Second SACCO',
            registration_number='CAP002',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
            membership_type=Sacco.MembershipType.OPEN,
        )
        other_membership = Membership.objects.create(
            user=self.user,
            sacco=other_sacco,
            status=Membership.Status.APPROVED,
            member_number='CAP-G-002',
            approved_date=timezone.now(),
        )
        other_savings_type = SavingsType.objects.create(
            sacco=other_sacco,
            name=SavingsType.Name.BOSA,
            minimum_contribution=Decimal('100.00'),
        )
        Saving.objects.create(
            membership=other_membership,
            savings_type=other_savings_type,
            amount=Decimal('10000.00'),
            status=Saving.Status.ACTIVE,
        )

        data = calculate_guarantee_capacity(self.user)

        # (20000 + 10000) * 0.5
        self.assertEqual(data['total_savings'], Decimal('30000.00'))
        self.assertEqual(data['max_guarantee_capacity'], Decimal('15000.0000'))
        self.assertEqual(data['available_capacity'], Decimal('15000.0000'))

    def test_pending_guarantee_does_not_reduce_capacity(self):
        """Only APPROVED guarantees reserve capacity, not PENDING ones."""
        Saving.objects.create(
            membership=self.guarantor_membership,
            savings_type=self.savings_type,
            amount=Decimal('20000.00'),
            status=Saving.Status.ACTIVE,
        )
        active_loan = self._create_loan(Loan.Status.ACTIVE)
        Guarantor.objects.create(
            loan=active_loan,
            guarantor=self.user,
            status=Guarantor.Status.PENDING,
            guarantee_amount=Decimal('4000.00'),
        )

        data = calculate_guarantee_capacity(self.user)

        self.assertEqual(data['active_guarantees'], Decimal('0'))
        self.assertEqual(data['available_capacity'], Decimal('10000.0000'))

    def test_approved_guarantee_on_terminal_loan_frees_capacity(self):
        """A guarantee whose loan is COMPLETED/REJECTED is released."""
        Saving.objects.create(
            membership=self.guarantor_membership,
            savings_type=self.savings_type,
            amount=Decimal('20000.00'),
            status=Saving.Status.ACTIVE,
        )
        completed_loan = self._create_loan(Loan.Status.COMPLETED)
        Guarantor.objects.create(
            loan=completed_loan,
            guarantor=self.user,
            status=Guarantor.Status.APPROVED,
            guarantee_amount=Decimal('4000.00'),
        )

        data = calculate_guarantee_capacity(self.user)

        self.assertEqual(data['active_guarantees'], Decimal('0'))
        self.assertEqual(data['available_capacity'], Decimal('10000.0000'))
