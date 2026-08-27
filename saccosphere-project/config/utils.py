"""Shared utility functions."""

import hashlib
import re
from uuid import uuid4


class InvalidPhoneNumberError(ValueError):
    """Raised when a phone number cannot be normalized to a valid format."""
    pass


def normalize_phone_number(raw: str, region: str = 'KE') -> str:
    """Normalize a phone number to E.164 format.
    
    Accepts various input formats for Kenyan mobile numbers:
    - 07XXXXXXXX (10 digits, starts with 0)
    - 011XXXXXXXX (10 digits, starts with 01, newer prefixes)
    - 7XXXXXXXX (9 digits, starts with 7)
    - 1XXXXXXXX (9 digits, starts with 1, newer prefixes)
    - +254XXXXXXXXX (12 digits with + prefix)
    - 254XXXXXXXXX (12 digits without +)
    - Numbers with spaces, dashes, or other separators
    
    Returns:
        str: Phone number in E.164 format (+254712345678)
    
    Raises:
        InvalidPhoneNumberError: If the phone number is not a valid Kenyan mobile number
    
    Args:
        raw: The raw phone number string to normalize
        region: The region code (default: 'KE' for Kenya)
    """
    if not raw or not isinstance(raw, str):
        raise InvalidPhoneNumberError('Phone number must be a non-empty string')
    
    # Remove all non-digit characters
    clean_num = re.sub(r'[^\d]', '', raw)
    
    # Validate length and format for Kenyan mobile numbers
    # Kenyan mobile numbers are 9 digits starting with 7 or 1, plus country code 254
    if len(clean_num) == 9 and clean_num[0] in ('7', '1'):
        # Format: 712345678 or 112345678 -> +254712345678
        return f'+254{clean_num}'
    elif len(clean_num) == 10 and clean_num.startswith('0') and clean_num[1] in ('7', '1'):
        # Format: 0712345678 or 0112345678 -> +254712345678
        return f'+254{clean_num[1:]}'
    elif len(clean_num) == 12 and clean_num.startswith('254'):
        # Format: 254712345678 -> +254712345678
        return f'+{clean_num}'
    elif len(clean_num) == 13 and clean_num.startswith('+254'):
        # Already in E.164 format
        return clean_num
    
    # If we get here, the number doesn't match any valid Kenyan format
    raise InvalidPhoneNumberError(
        f'Invalid Kenyan phone number format: {raw}. '
        'Expected format: 07XXXXXXXX, 011XXXXXXXX, 7XXXXXXXX, 1XXXXXXXX, '
        '+254XXXXXXXXX, or 254XXXXXXXXX'
    )


def get_request_id(request):
    return request.headers.get('X-Correlation-ID') or str(uuid4())


def sanitize_pii(value, max_length=8):
    """
    Sanitize PII for logging by returning a truncated/hashed reference.
    
    Args:
        value: The PII value to sanitize (e.g., id_number, phone number)
        max_length: Maximum length of the truncated reference (default: 8)
    
    Returns:
        str: A truncated reference (first N chars + '...' + last 4 chars)
             or a SHA256 hash if the value is too short for truncation
    """
    if not value:
        return '[REDACTED]'
    
    value_str = str(value)
    
    # If value is short enough, just show first N and last 4 chars
    if len(value_str) > max_length + 4:
        return f'{value_str[:max_length]}...{value_str[-4:]}'
    
    # For short values, use a hash instead
    return f'[HASH:{hashlib.sha256(value_str.encode()).hexdigest()[:8]}]'
