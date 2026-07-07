"""
M-Pesa Daraja Security Module

Handles M-Pesa callback verification including:
- IP allowlisting (Safaricom IP ranges)
- Replay attack detection
- Signature verification (when present)

Expected M-Pesa Callback Structures:

STK Push Callback (M-Pesa Online Checkout):
{
  "Body": {
    "stkCallback": {
      "MerchantRequestID": "...",
      "CheckoutRequestID": "...",
      "ResultCode": 0,
      "ResultDesc": "The service request has been accepted successfully.",
      "CallbackMetadata": {
        "Item": [
          {"Name": "Amount", "Value": 1.00},
          {"Name": "MpesaReceiptNumber", "Value": "LHG31AL60V"},
          {"Name": "TransactionDate", "Value": 20191219102115},
          {"Name": "PhoneNumber", "Value": 254708374149}
        ]
      }
    }
  }
}

B2C (Loan Disbursement) Callback:
{
  "Result": {
    "ResultType": 0,
    "ResultCode": 0,
    "ResultDesc": "The service request has been accepted successfully.",
    "OriginatorConversationID": "...",
    "ConversationID": "...",
    "TransactionID": "..."
  }
}

Security Checks:
1. IP Allowlisting: Requests from Safaricom IPs only
2. Replay Detection: Cache-based check for duplicate callbacks
3. Signature Verification: Only verified if password/timestamp present
   (typically not present in callback response, only in request)
"""
import base64
import hmac
import ipaddress
import logging

from django.conf import settings
from django.core.cache import cache


logger = logging.getLogger('saccosphere.security')

SAFARICOM_IP_RANGES = [
    '196.201.212.0/24',
    '196.201.213.0/24',
    '196.201.214.0/24',
    '196.201.214.0/23',
    '192.168.201.0/24',  
]

def verify_mpesa_signature(request):
    """
    Verify M-Pesa callback signature.
    
    Note: M-Pesa STK/B2C callbacks don't include password/timestamp fields.
    These are only used in the request phase. For callbacks, signature 
    verification relies on IP allowlisting and replay attack detection.
    
    Returns True if signature is valid or if callback lacks signature fields
    (which is normal for M-Pesa callbacks in development/sandbox).
    """
    payload = getattr(request, '_mpesa_callback_body', request.data)
    callback = _get_callback_payload(payload)
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

    # M-Pesa callbacks don't typically include password/timestamp in response
    if not received_password or not timestamp:
        logger.debug(
            'M-Pesa callback lacks signature fields (normal for callbacks). '
            'Relying on IP verification and replay detection instead. '
            'Callback keys: %s',
            list(callback.keys()) if isinstance(callback, dict) else 'N/A',
        )
        return True

    # If signature fields are present, verify them
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
        return False
    
    logger.debug('M-Pesa callback signature verified successfully.')
    return True


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


def _get_callback_payload(payload):
    if not isinstance(payload, dict):
        logger.debug('Callback payload is not a dict: %s', type(payload))
        return {}

    body = payload.get('Body') or payload.get('body') or {}
    
    # Try to extract callback from nested structures
    stk_callback = (
        body.get('stkCallback')
        or body.get('StkCallback')
    )
    
    if stk_callback:
        logger.debug('Extracted STK callback from Body')
        return stk_callback
    
    # Try B2C structure
    result = payload.get('Result') or payload.get('result')
    if result:
        logger.debug('Extracted Result callback (B2C)')
        return result
    
    # If no nested structure found, check if Body itself is the callback
    if body:
        logger.debug('Using Body as callback (no nesting)')
        return body
    
    # Last resort: check if payload itself has callback fields
    if payload.get('password') or payload.get('Password') or payload.get('timestamp') or payload.get('Timestamp'):
        logger.debug('Callback fields found at top level of payload')
        return payload
    
    logger.debug(
        'Could not extract callback from payload. Payload structure: %s',
        list(payload.keys()),
    )
    return {}


def _get_first_value(data, *keys):
    for key in keys:
        if key in data:
            return data[key]

    return None
