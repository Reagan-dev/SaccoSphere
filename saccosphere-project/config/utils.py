"""Shared utility functions."""

import hashlib
import ipaddress
import logging
import re
from uuid import uuid4


metrics_logger = logging.getLogger('saccosphere.metrics')

# Railway (this project's deploy target - see README "Railway Deployment
# Processes") fronts every service with a single Envoy edge hop. Its
# internal proxy addresses live in the RFC 6598 carrier-grade NAT block,
# so an X-Forwarded-For entry inside this range was added by Railway, not
# by a real client.
_RAILWAY_INTERNAL_NETWORK = ipaddress.ip_network('100.64.0.0/10')


def _is_ip_address(value):
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def _is_railway_internal_ip(value):
    try:
        return ipaddress.ip_address(value) in _RAILWAY_INTERNAL_NETWORK
    except ValueError:
        return False


def get_client_ip(request):
    """Return the real client IP, trusting only Railway's edge proxy.

    Railway puts exactly one trusted hop (its Envoy edge) in front of the
    app. That edge:

    * sets ``X-Envoy-External-Address`` to the single client IP it
      observed and overwrites any value the client sent, so it is the
      authoritative source whenever present;
    * *appends* the observed peer to ``X-Forwarded-For`` without stripping
      client-supplied entries. Only the rightmost entry was added by
      infrastructure we control; everything to its left is
      attacker-controllable and must never be trusted.

    Resolution order:

    1. ``X-Envoy-External-Address`` when it is a valid IP.
    2. ``X-Forwarded-For`` scanned right-to-left, skipping Railway
       internal (``100.64.0.0/10``) hops, taking the first public IP.
    3. ``REMOTE_ADDR``.

    Returns the IP string, or ``None`` when nothing usable is present.
    Callers that need a non-empty value (e.g. a cache key) should coerce
    ``None`` themselves.
    """
    envoy_address = (
        request.META.get('HTTP_X_ENVOY_EXTERNAL_ADDRESS') or ''
    ).strip()
    if _is_ip_address(envoy_address):
        return envoy_address

    forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR', '') or ''
    entries = [entry.strip() for entry in forwarded_for.split(',')]
    for candidate in reversed(entries):
        if not _is_ip_address(candidate):
            continue
        if _is_railway_internal_ip(candidate):
            continue
        return candidate

    remote_addr = (request.META.get('REMOTE_ADDR') or '').strip()
    return remote_addr or None


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


def emit_metric(event, **tags):
    """
    Emit a structured, greppable counter-increment log line.

    No statsd/Prometheus client is a project dependency (confirmed absent
    from requirements.txt) - this logs a consistently-prefixed structured
    line instead of incrementing a real counter, so it can still be alerted
    on via log-based metrics until real metrics infrastructure exists. When
    that infra lands, replace this function's body with the real client
    call; call sites do not need to change.
    """
    tag_str = ' '.join(f'{key}={value}' for key, value in tags.items())
    metrics_logger.info('METRIC event=%s %s', event, tag_str)


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
