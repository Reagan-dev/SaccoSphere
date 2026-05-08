"""
Loan amortisation engine for reducing balance loans.

This module implements financial calculations for loan repayment schedules
using the reducing balance (amortising) method. Unlike simple interest,
reducing balance means interest is calculated on the outstanding principal
after each payment, making it more fair to borrowers.

Key concepts:
- Monthly payment is fixed throughout the loan term
- Each payment consists of interest + principal portions
- Interest portion decreases over time as balance reduces
- Principal portion increases over time
- Final payment is adjusted to clear any rounding remainders

All calculations use Python's Decimal type for financial precision,
never floating point numbers, to avoid rounding errors in monetary values.
"""

from decimal import Decimal, ROUND_HALF_UP
from datetime import date, timedelta
from typing import List, Dict
import calendar


def calculate_monthly_payment(principal, annual_rate, months):
    """
    Calculate fixed monthly payment for reducing balance loan.
    
    Uses the standard amortisation formula:
    M = P × r(1+r)^n / ((1+r)^n − 1)
    
    Args:
        principal: Decimal - loan principal amount
        annual_rate: Decimal - annual interest rate (e.g., 12.0 for 12%)
        months: int - loan term in months
        
    Returns:
        Decimal - fixed monthly payment amount
    """
    if principal == 0 or annual_rate == 0 or months == 0:
        return Decimal('0.00')
    
    # Convert annual rate to monthly rate (decimal)
    monthly_rate = annual_rate / Decimal('1200')
    
    # Calculate (1 + r)^n using Decimal arithmetic
    rate_plus_one = Decimal('1') + monthly_rate
    rate_power_n = rate_plus_one ** months
    
    # Apply amortisation formula
    numerator = principal * monthly_rate * rate_power_n
    denominator = rate_power_n - Decimal('1')
    
    monthly_payment = numerator / denominator
    
    # Round to 2 decimal places using standard financial rounding
    return monthly_payment.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def calculate_simple_interest(principal, annual_rate, months):
    """
    Calculate simple interest for comparison/display purposes.
    
    Simple interest = principal × rate × time
    Unlike reducing balance, this assumes interest is calculated on
    the original principal throughout the loan term.
    
    Args:
        principal: Decimal - loan principal amount
        annual_rate: Decimal - annual interest rate (e.g., 12.0 for 12%)
        months: int - loan term in months
        
    Returns:
        tuple: (total_interest, total_payable) as Decimals
    """
    if principal == 0 or annual_rate == 0 or months == 0:
        return Decimal('0.00'), principal
    
    # Calculate simple interest
    years = Decimal(months) / Decimal('12')
    total_interest = principal * annual_rate * years / Decimal('100')
    total_payable = principal + total_interest
    
    # Round to 2 decimal places
    total_interest = total_interest.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    total_payable = total_payable.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    
    return total_interest, total_payable


def compute_due_date(start_date, instalment_number, due_day=25):
    """
    Calculate due date for a specific instalment.
    
    Handles month-end dates and leap years correctly.
    
    Args:
        start_date: date - loan disbursement date
        instalment_number: int - instalment number (1-based)
        due_day: int - day of month for payments (default 25)
        
    Returns:
        date - calculated due date
    """
    if instalment_number == 1:
        # First instalment: check if due_day fits in current month
        last_day_of_current_month = calendar.monthrange(start_date.year, start_date.month)[1]
        
        if start_date.day < due_day and due_day <= last_day_of_current_month:
            # Due day fits in current month
            year, month = start_date.year, start_date.month
        else:
            # Due day doesn't fit or start_date.day >= due_day, go to next month
            if start_date.month == 12:
                year, month = start_date.year + 1, 1
            else:
                year, month = start_date.year, start_date.month + 1
    else:
        # Subsequent instalments: advance months from first due date
        first_due = compute_due_date(start_date, 1, due_day)
        months_to_add = instalment_number - 1
        
        year = first_due.year
        month = first_due.month + months_to_add
        
        # Handle year overflow
        while month > 12:
            year += 1
            month -= 12
    
    # Adjust due_day if it's beyond the last day of the month
    last_day_of_month = calendar.monthrange(year, month)[1]
    actual_due_day = min(due_day, last_day_of_month)
    
    return date(year, month, actual_due_day)


def generate_repayment_schedule(loan_amount, annual_interest_rate, term_months, start_date, due_day=25):
    """
    Generate a reducing balance (amortising) repayment schedule.
    
    Creates a complete payment schedule showing how each monthly payment
    is split between interest and principal portions, with the remaining
    balance after each payment.
    
    Parameters:
        loan_amount: Decimal - principal amount
        annual_interest_rate: Decimal - annual interest rate (e.g., 12.0 for 12%)
        term_months: int - number of monthly instalments
        start_date: date - loan disbursement date
        due_day: int - day of month for instalments (default 25)
    
    Returns:
        List[Dict]: List of dictionaries with instalment details:
            [{
                'instalment_number': int,
                'due_date': date,
                'amount': Decimal,
                'principal': Decimal,
                'interest': Decimal,
                'balance_after': Decimal
            }]
    """
    # Validate inputs
    if loan_amount <= 0 or term_months <= 0:
        return []
    
    # Calculate monthly payment
    monthly_payment = calculate_monthly_payment(loan_amount, annual_interest_rate, term_months)
    
    # Initialize schedule
    schedule = []
    remaining_balance = loan_amount
    monthly_rate = annual_interest_rate / Decimal('1200')
    
    for instalment_num in range(1, term_months + 1):
        # Calculate due date
        due_date = compute_due_date(start_date, instalment_num, due_day)
        
        # Handle zero interest rate case
        if annual_interest_rate == 0:
            interest_payment = Decimal('0.00')
            principal_payment = loan_amount / Decimal(term_months)
            principal_payment = principal_payment.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        else:
            # Calculate interest for this period
            interest_payment = remaining_balance * monthly_rate
            interest_payment = interest_payment.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            
            # Principal payment is monthly payment minus interest
            principal_payment = monthly_payment - interest_payment
        
        # Handle final instalment - adjust to clear remaining balance
        if instalment_num == term_months:
            # Adjust for any rounding errors
            principal_payment = remaining_balance
            final_payment = principal_payment + interest_payment
            final_payment = final_payment.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            monthly_payment = final_payment
        
        # Update remaining balance
        remaining_balance -= principal_payment
        remaining_balance = remaining_balance.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        
        # Ensure balance doesn't go negative due to rounding
        if remaining_balance < Decimal('0.01'):
            remaining_balance = Decimal('0.00')
        
        # Add instalment to schedule
        schedule.append({
            'instalment_number': instalment_num,
            'due_date': due_date,
            'amount': monthly_payment,
            'principal': principal_payment,
            'interest': interest_payment,
            'balance_after': remaining_balance,
        })
    
    return schedule
