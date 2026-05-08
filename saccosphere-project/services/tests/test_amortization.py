"""Test loan amortisation engine functionality."""

from decimal import Decimal
from datetime import date
from django.test import TestCase

from services.engines.amortization import (
    calculate_monthly_payment,
    calculate_simple_interest,
    generate_repayment_schedule,
)


class AmortizationEngineTestCase(TestCase):
    """Test amortisation engine calculations and schedule generation."""

    def test_schedule_length(self):
        """Test that generated schedule has correct number of instalments."""
        schedule = generate_repayment_schedule(
            loan_amount=Decimal('100000.00'),
            annual_interest_rate=Decimal('12.0'),
            term_months=12,
            start_date=date(2024, 1, 15),
        )
        
        self.assertEqual(len(schedule), 12)

    def test_last_balance_is_zero(self):
        """Test that last instalment clears remaining balance."""
        schedule = generate_repayment_schedule(
            loan_amount=Decimal('50000.00'),
            annual_interest_rate=Decimal('10.0'),
            term_months=6,
            start_date=date(2024, 1, 1),
        )
        
        last_instalment = schedule[-1]
        self.assertEqual(last_instalment['balance_after'], Decimal('0.00'))

    def test_total_principal_equals_loan_amount(self):
        """Test that sum of all principal payments equals loan amount."""
        loan_amount = Decimal('75000.00')
        schedule = generate_repayment_schedule(
            loan_amount=loan_amount,
            annual_interest_rate=Decimal('15.0'),
            term_months=24,
            start_date=date(2024, 2, 1),
        )
        
        total_principal = sum(
            instalment['principal'] for instalment in schedule
        )
        
        # Allow for tiny rounding differences
        self.assertLessEqual(
            abs(total_principal - loan_amount),
            Decimal('0.01')
        )

    def test_uses_decimal_not_float(self):
        """Test that all monetary values are Decimal instances."""
        schedule = generate_repayment_schedule(
            loan_amount=Decimal('100000.00'),
            annual_interest_rate=Decimal('12.0'),
            term_months=12,
            start_date=date(2024, 1, 15),
        )
        
        for instalment in schedule:
            self.assertIsInstance(instalment['amount'], Decimal)
            self.assertIsInstance(instalment['principal'], Decimal)
            self.assertIsInstance(instalment['interest'], Decimal)
            self.assertIsInstance(instalment['balance_after'], Decimal)

    def test_monthly_payment_calculation(self):
        """Test monthly payment calculation accuracy."""
        # Test case: 100,000 at 12% for 12 months
        payment = calculate_monthly_payment(
            principal=Decimal('100000.00'),
            annual_rate=Decimal('12.0'),
            months=12
        )
        
        # Expected payment for this scenario
        expected_payment = Decimal('8884.88')
        self.assertEqual(payment, expected_payment)

    def test_simple_interest_calculation(self):
        """Test simple interest calculation for comparison."""
        total_interest, total_payable = calculate_simple_interest(
            principal=Decimal('50000.00'),
            annual_rate=Decimal('10.0'),
            months=12
        )
        
        # Simple interest: 50000 * 10% * 1 year = 5000
        expected_interest = Decimal('5000.00')
        expected_payable = Decimal('55000.00')
        
        self.assertEqual(total_interest, expected_interest)
        self.assertEqual(total_payable, expected_payable)

    def test_zero_interest_rate(self):
        """Test amortisation with zero interest rate."""
        schedule = generate_repayment_schedule(
            loan_amount=Decimal('12000.00'),
            annual_interest_rate=Decimal('0.0'),
            term_months=12,
            start_date=date(2024, 1, 1),
        )
        
        # With zero interest, each payment should be equal principal
        expected_principal_payment = Decimal('1000.00')
        
        for instalment in schedule:
            self.assertEqual(instalment['interest'], Decimal('0.00'))
            self.assertEqual(instalment['principal'], expected_principal_payment)

    def test_due_date_calculation(self):
        """Test due date calculation logic."""
        # Test first instalment when start date is before due day
        schedule = generate_repayment_schedule(
            loan_amount=Decimal('10000.00'),
            annual_interest_rate=Decimal('12.0'),
            term_months=2,
            start_date=date(2024, 1, 10),  # Before 25th
        )
        
        # First due date should be same month (January 25, 2024)
        self.assertEqual(schedule[0]['due_date'], date(2024, 1, 25))
        
        # Second due date should be next month (February 25, 2024)
        self.assertEqual(schedule[1]['due_date'], date(2024, 2, 25))

    def test_due_date_calculation_after_due_day(self):
        """Test due date when start date is after due day."""
        schedule = generate_repayment_schedule(
            loan_amount=Decimal('10000.00'),
            annual_interest_rate=Decimal('12.0'),
            term_months=2,
            start_date=date(2024, 1, 30),  # After 25th
        )
        
        # First due date should be next month (February 25, 2024)
        self.assertEqual(schedule[0]['due_date'], date(2024, 2, 25))
        
        # Second due date should be following month (March 25, 2024)
        self.assertEqual(schedule[1]['due_date'], date(2024, 3, 25))

    def test_february_handling(self):
        """Test due date calculation for February (leap year)."""
        schedule = generate_repayment_schedule(
            loan_amount=Decimal('10000.00'),
            annual_interest_rate=Decimal('12.0'),
            term_months=2,
            start_date=date(2024, 1, 30),  # Leap year
        )
        
        # Should handle February 29 correctly in leap year
        self.assertEqual(schedule[0]['due_date'], date(2024, 2, 25))

    def test_month_end_handling(self):
        """Test due date when due_day exceeds month length."""
        schedule = generate_repayment_schedule(
            loan_amount=Decimal('10000.00'),
            annual_interest_rate=Decimal('12.0'),
            term_months=1,
            start_date=date(2024, 1, 30),  # Before due_day
            due_day=31  # January has 31 days
        )
        
        # Since start_date.day (30) < due_day (31), first due stays in current month
        # January has 31 days, so due_day=31 is valid
        self.assertEqual(schedule[0]['due_date'], date(2024, 1, 31))

    def test_due_date_adjustment(self):
        """Test due date adjustment when due_day exceeds month length."""
        schedule = generate_repayment_schedule(
            loan_amount=Decimal('10000.00'),
            annual_interest_rate=Decimal('12.0'),
            term_months=1,
            start_date=date(2024, 1, 30),  # Before due_day
            due_day=31  # February doesn't have 31 days
        )
        
        # Should adjust to last day of February (29 in leap year 2024)
        self.assertEqual(schedule[0]['due_date'], date(2024, 2, 29))

    def test_interest_portion_decreases_over_time(self):
        """Test that interest portion decreases while principal increases."""
        schedule = generate_repayment_schedule(
            loan_amount=Decimal('100000.00'),
            annual_interest_rate=Decimal('12.0'),
            term_months=12,
            start_date=date(2024, 1, 1),
        )
        
        # Interest should decrease from first to last instalment
        first_interest = schedule[0]['interest']
        last_interest = schedule[-1]['interest']
        
        # Principal should increase from first to last instalment
        first_principal = schedule[0]['principal']
        last_principal = schedule[-1]['principal']
        
        self.assertLess(last_interest, first_interest)
        self.assertGreater(last_principal, first_principal)

    def test_zero_loan_amount(self):
        """Test handling of zero loan amount."""
        schedule = generate_repayment_schedule(
            loan_amount=Decimal('0.00'),
            annual_interest_rate=Decimal('12.0'),
            term_months=12,
            start_date=date(2024, 1, 1),
        )
        
        self.assertEqual(len(schedule), 0)

    def test_zero_term_months(self):
        """Test handling of zero term months."""
        schedule = generate_repayment_schedule(
            loan_amount=Decimal('10000.00'),
            annual_interest_rate=Decimal('12.0'),
            term_months=0,
            start_date=date(2024, 1, 1),
        )
        
        self.assertEqual(len(schedule), 0)
