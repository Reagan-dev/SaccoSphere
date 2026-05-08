# ============================================================
# REVIEW — READ THIS THEN DELETE FROM THIS LINE TO THE END
# ============================================================

## Amortization Engine Implementation Review

### What Each Class or Function Does and Why

#### **services/engines/amortization.py**

**calculate_monthly_payment(principal, annual_rate, months)**
- Calculates fixed monthly payment using standard amortisation formula: M = P × r(1+r)^n / ((1+r)^n − 1)
- Uses Decimal arithmetic throughout for financial precision
- Returns payment rounded to 2 decimal places using ROUND_HALF_UP (standard financial rounding)
- Critical for generating repayment schedules where each instalment has same total amount

**calculate_simple_interest(principal, annual_rate, months)**
- Calculates simple interest for comparison/display purposes
- Formula: principal × rate × time
- Returns total interest and total payable amounts
- Useful for showing borrowers difference between simple vs reducing balance interest

**compute_due_date(start_date, instalment_number, due_day=25)**
- Calculates due dates for each instalment
- Handles month boundaries correctly (e.g., February 28/29, months with 30/31 days)
- First instalment: same month if start_date.day < due_day, else next month
- Subsequent instalments: advance months from first due date
- Adjusts due_day if it exceeds last day of target month

**generate_repayment_schedule(loan_amount, annual_interest_rate, term_months, start_date, due_day=25)**
- Core function that creates complete reducing balance repayment schedule
- Each instalment has: amount, principal, interest, balance_after
- Interest portion decreases over time as outstanding balance reduces
- Principal portion increases over time
- Final instalment adjusted to clear any rounding remainders (balance_after = 0.00)
- Returns list of dictionaries ready for database insertion

#### **services/views.py**

**RepaymentScheduleView(ListAPIView)**
- API endpoint to retrieve or generate repayment schedules for loans
- GET /api/services/loans/{id}/schedule/
- If schedule exists: returns from database
- If no schedule AND loan status is appropriate: generates new schedule
- Uses atomic transaction for bulk creating RepaymentSchedule records
- Only generates for APPROVED, ACTIVE, or DISBURSEMENT_PENDING loans

#### **services/tests/test_amortization.py**

**Comprehensive test coverage:**
- test_schedule_length: Verifies correct number of instalments
- test_last_balance_is_zero: Ensures final balance clears to exactly zero
- test_total_principal_equals_loan_amount: Confirms sum of principals equals loan amount within 0.01
- test_uses_decimal_not_float: Validates all monetary values are Decimal instances
- test_monthly_payment_calculation: Checks payment formula accuracy
- test_zero_interest_rate: Handles zero-interest edge case
- test_due_date_calculation: Verifies due date logic
- test_simple_interest_calculation: Tests simple interest formula
- test_interest_portion_decreases_over_time: Validates reducing balance behavior

### Django/Python Concepts You Might Not Know Well

**Decimal Arithmetic**
- Python's Decimal type prevents floating-point rounding errors in financial calculations
- ROUND_HALF_UP is standard financial rounding (0.005 rounds to 0.01, 0.004 rounds to 0.00)
- Essential for monetary calculations where precision matters

**Atomic Transactions**
- `transaction.atomic()` ensures all database operations succeed or fail together
- Critical for creating complete repayment schedules consistently
- Prevents partial schedules if something goes wrong mid-process

**Bulk Operations**
- `bulk_create()` inserts multiple records efficiently
- Much faster than individual save() calls for many records
- Used here to create all instalments at once

**Date Handling with calendar**
- `calendar.monthrange()` gets last day of month (handles leap years)
- Essential for accurate due date calculations across all months

### One Thing to Test Manually to Confirm It Works

**Create a test loan and verify schedule generation:**

1. **Via Django Admin:**
   - Create a Loan with amount=100000, interest_rate=12.0, term_months=12
   - Set status to APPROVED
   - Set disbursement_date to today's date

2. **Via API:**
   ```bash
   # Get auth token first, then:
   GET /api/services/loans/{loan_id}/schedule/
   ```

3. **Verify:**
   - Schedule has exactly 12 instalments
   - Each instalment has Decimal amounts (not floats)
   - Last instalment balance_after is 0.00
   - Sum of all principal fields equals 100000.00 (within 0.01)
   - Interest portions decrease over time
   - Principal portions increase over time

### Important Design Decisions and Why

**Decimal Throughout Implementation**
- Decision: Use Decimal for ALL monetary calculations
- Why: Prevents floating-point errors that could cause financial discrepancies
- Impact: Ensures accurate loan calculations down to the cent

**Reducing Balance vs Simple Interest**
- Decision: Implement reducing balance (amortising) method
- Why: Fairer to borrowers - interest calculated on outstanding principal only
- Impact: Lower total interest compared to simple interest on same loan

**Final Instalment Adjustment**
- Decision: Adjust final payment to clear rounding remainders
- Why: Ensures balance_after is exactly zero, preventing tiny residual amounts
- Impact: Guarantees loan is fully paid after all scheduled instalments

**Atomic Schedule Generation**
- Decision: Use database transaction for schedule creation
- Why: Prevents partial schedules if system fails mid-process
- Impact: Data consistency - either complete schedule or nothing

**Comprehensive Test Coverage**
- Decision: Test edge cases (zero interest, month boundaries, leap years)
- Why: Financial calculations must be reliable in all scenarios
- Impact: Confidence in production correctness

**View Caching Strategy**
- Decision: Generate schedule once, then cache in database
- Why: Avoids expensive recalculation on every request
- Impact: Better performance and consistent schedules

# END OF REVIEW — DELETE EVERYTHING FROM THE FIRST # LINE ABOVE
# ============================================================
