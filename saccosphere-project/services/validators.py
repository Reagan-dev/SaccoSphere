"""Shared config-field validators for the services app.

Rate ceiling
------------
``MAX_ANNUAL_RATE_PERCENT`` is a PLACEHOLDER. A dividend or savings
interest rate above roughly 100%/yr is almost certainly a data-entry
slip, but the real regulatory / product ceiling for a Kenyan SACCO is a
policy decision (SASRA guidance and the SACCO's own by-laws), not an
engineering guess. Until that is signed off, 0-100% is enforced. Flagged
in the task summary.

Financial year
--------------
Canonical format is ``YYYY/YYYY`` with the second year exactly one more
than the first (a SACCO financial year straddles two calendar years).
Every existing value in the codebase already uses this form. ``YYYY`` on
its own is intentionally rejected: "2025" vs "2025/2026" for the same
period is exactly the ambiguity that the dividend-declaration uniqueness
constraint (services migration 0013) relies on not existing.
"""

import re
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator


MIN_ANNUAL_RATE_PERCENT = Decimal('0.00')
# PLACEHOLDER - needs regulatory / product sign-off (see module docstring).
MAX_ANNUAL_RATE_PERCENT = Decimal('100.00')

ANNUAL_RATE_VALIDATORS = [
    MinValueValidator(
        MIN_ANNUAL_RATE_PERCENT,
        message='Annual rate cannot be negative.',
    ),
    MaxValueValidator(
        MAX_ANNUAL_RATE_PERCENT,
        message=(
            'Annual rate cannot exceed %(limit_value)s percent '
            '(placeholder ceiling, pending policy sign-off).'
        ),
    ),
]

FINANCIAL_YEAR_REGEX = re.compile(r'^(\d{4})/(\d{4})$')
MIN_FINANCIAL_YEAR = 2000
MAX_FINANCIAL_YEAR = 2100

FINANCIAL_YEAR_HELP_TEXT = (
    'Canonical financial year: YYYY/YYYY with two consecutive years, '
    'e.g. 2025/2026.'
)


def is_canonical_financial_year(value):
    """Return True if ``value`` is a canonical ``YYYY/YYYY`` financial year."""
    match = FINANCIAL_YEAR_REGEX.match(value or '')
    if not match:
        return False
    start_year, end_year = int(match.group(1)), int(match.group(2))
    if not MIN_FINANCIAL_YEAR <= start_year <= MAX_FINANCIAL_YEAR:
        return False
    return end_year == start_year + 1


def validate_financial_year(value):
    """Enforce the canonical ``YYYY/YYYY`` (consecutive) financial year."""
    match = FINANCIAL_YEAR_REGEX.match(value or '')
    if not match:
        raise ValidationError(
            'Financial year must be in the format YYYY/YYYY, e.g. '
            '2025/2026.',
            code='financial_year_format',
        )
    start_year, end_year = int(match.group(1)), int(match.group(2))
    if not MIN_FINANCIAL_YEAR <= start_year <= MAX_FINANCIAL_YEAR:
        raise ValidationError(
            f'Financial year "{value}" is outside the supported range '
            f'{MIN_FINANCIAL_YEAR}-{MAX_FINANCIAL_YEAR}.',
            code='financial_year_range',
        )
    if end_year != start_year + 1:
        raise ValidationError(
            f'Financial year "{value}" must span two consecutive years, '
            'e.g. 2025/2026.',
            code='financial_year_not_consecutive',
        )
