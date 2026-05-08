from datetime import date
import calendar
from services.engines.amortization import compute_due_date

# Debug the failing test case
start_date = date(2024, 1, 30)  # After due_day
due_day = 31

result = compute_due_date(start_date, 1, due_day)
print(f"Start date: {start_date}")
print(f"Due day: {due_day}")
print(f"Result: {result}")
print(f"Expected: {date(2024, 2, 29)}")

# Let's trace the logic
print(f"\nTracing logic:")
print(f"start_date.day = {start_date.day}")
print(f"start_date.day < due_day = {start_date.day < due_day}")
print(f"start_date.month = {start_date.month}")

if start_date.day < due_day:
    print("Would use current month")
else:
    print("Would go to next month")
    if start_date.month == 12:
        year, month = start_date.year + 1, 1
    else:
        year, month = start_date.year, start_date.month + 1
    print(f"Next month: {year}-{month}")
    
    last_day_of_month = calendar.monthrange(year, month)[1]
    actual_due_day = min(due_day, last_day_of_month)
    print(f"Last day of month: {last_day_of_month}")
    print(f"Actual due day: {actual_due_day}")
    print(f"Final date: {date(year, month, actual_due_day)}")
