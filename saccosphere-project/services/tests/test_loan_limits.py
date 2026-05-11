"""Test loan limit and eligibility calculations."""

from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from accounts.models import Sacco, User
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
