import base64
import hmac
import ipaddress
import logging

from django.conf import settings
from django.core.cache import cache


logger = logging.getLogger('saccosphere.security')

SAFARICOM_IP_RANGES = [
    '196.201.214.0/24',
    '196.201.214.0/23',
    '192.168.201.0/24',
]


def verify_mpesa_signature(request):
    payload = getattr(request, '_mpesa_callback_body', request.data)
    callback = _get_stk_callback(payload)
    received_password = _get_first_value(
        callback,
        'password',
        'Password',
    )
    timestamp = _get_first_value(
        callback,
        'timestamp',
        'Timestamp',
    )

    if not received_password or not timestamp:
        logger.warning('M-Pesa callback signature fields are missing.')
        return False

    raw_password = (
        f'{settings.MPESA_SHORTCODE}{settings.MPESA_PASSKEY}{timestamp}'
    )
    expected_password = base64.b64encode(raw_password.encode())
    signature_matches = hmac.compare_digest(
        received_password.encode(),
        expected_password,
    )

    if not signature_matches:
        logger.warning('M-Pesa callback signature verification failed.')

    return signature_matches


def is_safaricom_ip(request):
    if settings.DEBUG:
        return True

    ip_address = _get_client_ip(request)
    if not ip_address:
        logger.warning('M-Pesa callback request IP is missing.')
        return False

    try:
        request_ip = ipaddress.ip_address(ip_address)
    except ValueError:
        logger.warning('Invalid M-Pesa callback request IP: %s.', ip_address)
        return False

    for ip_range in SAFARICOM_IP_RANGES:
        if request_ip in ipaddress.ip_network(ip_range):
            return True

    logger.warning(
        'M-Pesa callback request rejected from non-Safaricom IP: %s.',
        ip_address,
    )
    return False


def is_replay_attack(checkout_request_id):
    cache_key = f'mpesa_replay:{checkout_request_id}'
    if cache.get(cache_key):
        logger.warning(
            'M-Pesa callback replay detected for checkout_request_id=%s.',
            checkout_request_id,
        )
        return True

    cache.set(cache_key, True, timeout=86400)
    return False


def _get_client_ip(request):
    forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if forwarded_for:
        return forwarded_for.split(',')[0].strip()

    return request.META.get('REMOTE_ADDR')


def _get_stk_callback(payload):
    if not isinstance(payload, dict):
        return {}

    body = payload.get('Body') or payload.get('body') or {}
    return body.get('stkCallback') or body.get('StkCallback') or {}


def _get_first_value(data, *keys):
    for key in keys:
        if key in data:
            return data[key]

    return None
