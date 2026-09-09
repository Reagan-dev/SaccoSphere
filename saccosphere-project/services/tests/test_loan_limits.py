"""Test loan limit and eligibility calculations."""

from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models import Sacco, SaccoSettings, User
from saccomembership.models import Membership
from services.engines.loan_limits import calculate_loan_limit
from services.models import Loan, LoanType, Saving, SavingsType


class LoanLimitEngineTestCase(TestCase):
    """Test loan eligibility and limit business rules."""

    def setUp(self):
        """Set up a user, SACCO, membership, and loan product."""
        self.user = User.objects.create_user(
            email='borrower@example.com',
            first_name='Borrower',
            last_name='Member',
            password='testpass123',
        )
        self.sacco = Sacco.objects.create(
            name='Loan Limit SACCO',
            registration_number='LL001',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
            membership_type=Sacco.MembershipType.OPEN,
            loan_multiplier=Decimal('3.00'),
            min_loan_months=3,
        )
        self.membership = Membership.objects.create(
            user=self.user,
            sacco=self.sacco,
            status=Membership.Status.APPROVED,
            member_number='LL001-M001',
            approved_date=timezone.now() - timedelta(days=120),
        )
        self.savings_type = SavingsType.objects.create(
            sacco=self.sacco,
            name=SavingsType.Name.BOSA,
            minimum_contribution=Decimal('100.00'),
        )
        self.loan_type = LoanType.objects.create(
            sacco=self.sacco,
            name='Development Loan',
            interest_rate=Decimal('12.00'),
            max_term_months=36,
            min_amount=Decimal('1000.00'),
        )

    def create_saving(self, amount):
        """Create an active saving for the test membership."""
        return Saving.objects.create(
            membership=self.membership,
            savings_type=self.savings_type,
            amount=amount,
            status=Saving.Status.ACTIVE,
        )

    def create_loan(self, amount, outstanding_balance, status):
        """Create a loan for the test membership."""
        return Loan.objects.create(
            membership=self.membership,
            loan_type=self.loan_type,
            amount=amount,
            interest_rate=Decimal('12.00'),
            term_months=12,
            outstanding_balance=outstanding_balance,
            status=status,
        )

    def test_new_member_ineligible(self):
        """Test that members younger than the minimum age are ineligible."""
        self.membership.approved_date = timezone.now() - timedelta(days=30)
        self.membership.save(update_fields=['approved_date'])
        self.create_saving(Decimal('10000.00'))

        result = calculate_loan_limit(self.user, self.sacco)

        self.assertFalse(result['eligible'])
        self.assertEqual(result['reason'], 'MEMBERSHIP_TOO_NEW')
        self.assertEqual(result['max_amount'], Decimal('0'))

    def test_no_savings_ineligible(self):
        """Test that members with zero savings are ineligible."""
        result = calculate_loan_limit(self.user, self.sacco)

        self.assertFalse(result['eligible'])
        self.assertEqual(result['reason'], 'NO_SAVINGS')
        self.assertEqual(result['max_amount'], Decimal('0'))

    def test_3x_savings_rule(self):
        """Test that the limit is savings multiplied by SACCO multiplier."""
        self.create_saving(Decimal('10000.00'))

        result = calculate_loan_limit(self.user, self.sacco)

        self.assertTrue(result['eligible'])
        self.assertEqual(result['max_amount'], Decimal('30000.0000'))
        self.assertEqual(result['total_savings'], Decimal('10000.00'))

    def test_existing_loan_deducted(self):
        """Test that active loan balances reduce the available limit."""
        self.create_saving(Decimal('10000.00'))
        self.create_loan(
            amount=Decimal('5000.00'),
            outstanding_balance=Decimal('5000.00'),
            status=Loan.Status.ACTIVE,
        )

        result = calculate_loan_limit(self.user, self.sacco)

        self.assertTrue(result['eligible'])
        self.assertEqual(result['max_amount'], Decimal('25000.0000'))
        self.assertEqual(result['existing_balance'], Decimal('5000.00'))

    def test_pipeline_loans_count_against_limit(self):
        """A loan still in the approval pipeline reduces the limit.

        Item 3: existing_balance must count every status where the member
        owes or is about to owe, not just ACTIVE - otherwise a member can
        stack several mid-pipeline applications past their limit.
        """
        self.create_saving(Decimal('10000.00'))
        for pipeline_status in (
            Loan.Status.PENDING,
            Loan.Status.GUARANTORS_PENDING,
            Loan.Status.PENDING_APPROVAL,
            Loan.Status.UNDER_REVIEW,
            Loan.Status.APPROVED,
            Loan.Status.DISBURSEMENT_PENDING,
            Loan.Status.DISBURSED,
        ):
            with self.subTest(status=pipeline_status):
                loan = self.create_loan(
                    amount=Decimal('4000.00'),
                    outstanding_balance=Decimal('4000.00'),
                    status=pipeline_status,
                )
                result = calculate_loan_limit(self.user, self.sacco)
                self.assertEqual(
                    result['existing_balance'], Decimal('4000.00'),
                )
                # 30000 gross - 4000 reserved.
                self.assertEqual(result['max_amount'], Decimal('26000.0000'))
                loan.delete()

    def test_completed_and_rejected_loans_do_not_count(self):
        """Repaid / never-advanced loans must not reduce the limit."""
        self.create_saving(Decimal('10000.00'))
        self.create_loan(
            amount=Decimal('4000.00'),
            outstanding_balance=Decimal('0.00'),
            status=Loan.Status.COMPLETED,
        )
        self.create_loan(
            amount=Decimal('4000.00'),
            outstanding_balance=Decimal('4000.00'),
            status=Loan.Status.REJECTED,
        )

        result = calculate_loan_limit(self.user, self.sacco)

        self.assertEqual(result['existing_balance'], Decimal('0'))
        self.assertEqual(result['max_amount'], Decimal('30000.0000'))

    def test_default_blocks_new_loan(self):
        """Test that a defaulted loan blocks new loan eligibility."""
        self.create_saving(Decimal('10000.00'))
        self.create_loan(
            amount=Decimal('5000.00'),
            outstanding_balance=Decimal('5000.00'),
            status=Loan.Status.DEFAULTED,
        )

        result = calculate_loan_limit(self.user, self.sacco)

        self.assertFalse(result['eligible'])
        self.assertEqual(result['reason'], 'HAS_DEFAULT')
        self.assertEqual(result['max_amount'], Decimal('0'))

    def test_max_loan_amount_caps_savings_based_limit(self):
        """SaccoSettings.max_loan_amount hard-caps an otherwise-higher limit."""
        self.create_saving(Decimal('10000.00'))
        SaccoSettings.objects.create(
            sacco=self.sacco,
            max_loan_amount=Decimal('12000.00'),
        )

        result = calculate_loan_limit(self.user, self.sacco)

        self.assertTrue(result['eligible'])
        # Savings-based limit would be 30000.00 (10000 x 3); the SACCO cap
        # of 12000.00 must win.
        self.assertEqual(result['max_amount'], Decimal('12000.00'))

    def test_max_loan_amount_does_not_raise_a_lower_savings_based_limit(self):
        """A cap higher than the savings-based limit changes nothing."""
        self.create_saving(Decimal('10000.00'))
        SaccoSettings.objects.create(
            sacco=self.sacco,
            max_loan_amount=Decimal('500000.00'),
        )

        result = calculate_loan_limit(self.user, self.sacco)

        self.assertTrue(result['eligible'])
        self.assertEqual(result['max_amount'], Decimal('30000.0000'))


class LoanApplyMinAmountTestCase(TestCase):
    """
    min_loan_amount gates the requested amount at application time - it
    must never raise the computed eligibility limit itself.
    """

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            email='min-amount-borrower@example.com',
            first_name='Min',
            last_name='Borrower',
            password='testpass123',
        )
        self.sacco = Sacco.objects.create(
            name='Min Amount SACCO',
            registration_number='MA001',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
            membership_type=Sacco.MembershipType.OPEN,
            loan_multiplier=Decimal('3.00'),
            min_loan_months=3,
        )
        SaccoSettings.objects.create(
            sacco=self.sacco,
            min_loan_amount=Decimal('5000.00'),
        )
        self.membership = Membership.objects.create(
            user=self.user,
            sacco=self.sacco,
            status=Membership.Status.APPROVED,
            member_number='MA001-M001',
            approved_date=timezone.now() - timedelta(days=120),
        )
        self.savings_type = SavingsType.objects.create(
            sacco=self.sacco,
            name=SavingsType.Name.BOSA,
            minimum_contribution=Decimal('100.00'),
        )
        Saving.objects.create(
            membership=self.membership,
            savings_type=self.savings_type,
            amount=Decimal('10000.00'),
            status=Saving.Status.ACTIVE,
        )
        self.loan_type = LoanType.objects.create(
            sacco=self.sacco,
            name='Development Loan',
            interest_rate=Decimal('12.00'),
            max_term_months=36,
            min_amount=Decimal('1000.00'),
            requires_guarantors=False,
        )

    def test_request_below_min_loan_amount_rejected(self):
        self.client.force_authenticate(user=self.user)

        response = self.client.post(
            reverse('services:loan-apply'),
            {
                'loan_type': str(self.loan_type.id),
                'amount': '2000.00',
                'term_months': 6,
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(
            Loan.objects.filter(membership=self.membership).exists(),
        )

    def test_min_loan_amount_does_not_affect_computed_eligibility_limit(self):
        """
        min_loan_amount must gate request size only - the underlying
        eligibility computation (savings x multiplier) is untouched.
        """
        result = calculate_loan_limit(self.user, self.sacco)

        self.assertTrue(result['eligible'])
        self.assertEqual(result['max_amount'], Decimal('30000.0000'))

    def test_request_at_or_above_min_loan_amount_allowed(self):
        self.client.force_authenticate(user=self.user)

        response = self.client.post(
            reverse('services:loan-apply'),
            {
                'loan_type': str(self.loan_type.id),
                'amount': '5000.00',
                'term_months': 6,
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)


class LoanTypeMinAmountValidationTestCase(TestCase):
    """
    Item 4: LoanType.min_amount is enforced at application time by
    LoanApplySerializer.validate, mirroring the existing max_amount check.
    No SaccoSettings.min_loan_amount here, so the loan-type floor is the
    only thing that can reject a small request.
    """

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            email='lt-min-borrower@example.com',
            first_name='LT',
            last_name='Min',
            password='testpass123',
        )
        self.sacco = Sacco.objects.create(
            name='LT Min SACCO',
            registration_number='LTM001',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
            membership_type=Sacco.MembershipType.OPEN,
            loan_multiplier=Decimal('3.00'),
            min_loan_months=3,
        )
        self.membership = Membership.objects.create(
            user=self.user,
            sacco=self.sacco,
            status=Membership.Status.APPROVED,
            member_number='LTM001-M001',
            approved_date=timezone.now() - timedelta(days=120),
        )
        savings_type = SavingsType.objects.create(
            sacco=self.sacco,
            name=SavingsType.Name.BOSA,
            minimum_contribution=Decimal('100.00'),
        )
        Saving.objects.create(
            membership=self.membership,
            savings_type=savings_type,
            amount=Decimal('20000.00'),
            status=Saving.Status.ACTIVE,
        )
        self.loan_type = LoanType.objects.create(
            sacco=self.sacco,
            name='Floored Loan',
            interest_rate=Decimal('12.00'),
            max_term_months=36,
            min_amount=Decimal('3000.00'),
            max_amount=Decimal('40000.00'),
            requires_guarantors=False,
        )

    def _apply(self, amount):
        self.client.force_authenticate(user=self.user)
        return self.client.post(
            reverse('services:loan-apply'),
            {
                'loan_type': str(self.loan_type.id),
                'amount': amount,
                'term_months': 6,
            },
            format='json',
        )

    def test_below_loan_type_min_amount_is_rejected(self):
        response = self._apply('2000.00')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('below the minimum', str(response.data))
        self.assertFalse(
            Loan.objects.filter(membership=self.membership).exists(),
        )

    def test_at_loan_type_min_amount_is_accepted(self):
        response = self._apply('3000.00')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
