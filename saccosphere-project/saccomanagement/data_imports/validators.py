"""Validation helpers for SACCO member bulk import rows."""

import re
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.core.validators import validate_email


KENYA_ID_REGEX = re.compile(r'^\d{7,8}$')
PHONE_REGEX = re.compile(r'^(07|01|2547|2541|\+2547|\+2541)\d{7,8}$')
VALID_SAVINGS_TYPES = {'BOSA', 'FOSA', 'SHARE_CAPITAL', ''}


def validate_member_row(row, row_number):
    """Validate a single import row and return cleaned data with errors."""
    cleaned_row = dict(row)
    errors = []

    for field in ['first_name', 'last_name', 'email']:
        value = cleaned_row.get(field)
        if value is None or str(value).strip() == '':
            errors.append(f'Row {row_number}: {field} is required.')
        else:
            cleaned_row[field] = str(value).strip()

    email = cleaned_row.get('email')
    if email not in [None, '']:
        try:
            validate_email(email)
        except ValidationError:
            errors.append(f'Row {row_number}: email is invalid.')

    phone = cleaned_row.get('phone')
    if phone not in [None, '']:
        phone = str(phone).strip()
        cleaned_row['phone'] = phone
        if not PHONE_REGEX.match(phone):
            errors.append(f'Row {row_number}: phone format is invalid.')

    id_number = cleaned_row.get('id_number')
    if id_number not in [None, '']:
        id_number = str(id_number).strip()
        cleaned_row['id_number'] = id_number
        if not KENYA_ID_REGEX.match(id_number):
            errors.append(f'Row {row_number}: id_number format is invalid.')

    savings_amount = cleaned_row.get('savings_amount')
    if savings_amount not in [None, '']:
        try:
            parsed_savings = Decimal(str(savings_amount).strip())
            if parsed_savings < Decimal('0'):
                errors.append(
                    f'Row {row_number}: savings_amount must be >= 0.',
                )
            else:
                cleaned_row['savings_amount'] = parsed_savings
        except (InvalidOperation, TypeError, ValueError):
            errors.append(f'Row {row_number}: savings_amount must be numeric.')

    savings_type = cleaned_row.get('savings_type')
    if savings_type is None:
        cleaned_row['savings_type'] = ''
    else:
        savings_type = str(savings_type).strip().upper()
        cleaned_row['savings_type'] = savings_type
        if savings_type not in VALID_SAVINGS_TYPES:
            errors.append(
                f'Row {row_number}: savings_type must be one of '
                'BOSA, FOSA, SHARE_CAPITAL.',
            )

    return cleaned_row, errors


def validate_import_file(rows):
    """Validate all rows and return valid rows, error rows, and summary."""
    valid_rows = []
    error_rows = []

    for index, row in enumerate(rows, start=1):
        cleaned_row, errors = validate_member_row(row=row, row_number=index)
        if errors:
            error_rows.append(
                {
                    'row_number': index,
                    'row': cleaned_row,
                    'errors': errors,
                },
            )
        else:
            valid_rows.append(cleaned_row)

    error_summary = {
        'total_rows': len(rows),
        'valid_rows': len(valid_rows),
        'error_rows': len(error_rows),
    }
    return valid_rows, error_rows, error_summary
