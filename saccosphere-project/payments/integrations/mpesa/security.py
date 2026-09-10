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
3. Signature Verification: Daraja does NOT sign callbacks (verified against
   the Daraja contract - see verify_mpesa_signature). Password/Timestamp
   belong only to the STK Push *initiate request*, never to a callback, so
   their absence is expected and accepted.
"""
import base64
import hmac
import ipaddress
import logging

from django.conf import settings
from django.core.cache import cache

from config.utils import get_client_ip


logger = logging.getLogger('saccosphere.security')


def _get_safaricom_ip_ranges():
    """Get Safaricom IP ranges based on environment."""
    if settings.MPESA_ENVIRONMENT == 'sandbox':
        return getattr(settings, 'MPESA_IP_RANGES_SANDBOX', [])
    return getattr(settings, 'MPESA_IP_RANGES_PRODUCTION', [])

def verify_mpesa_signature(request):
    """Best-effort authenticity check for an M-Pesa Daraja callback.

    Daraja does NOT sign its callbacks. Verified against the Daraja
    contract: neither the STK Push callback (``Body.stkCallback``) nor the
    B2C result callback (``Result``) ever carries ``Password`` /
    ``Timestamp`` / ``SecurityCredential`` / any HMAC - those belong only
    to the STK Push *initiate request* this server sends to Daraja.
    Callback authenticity therefore rests on the other controls the view
    applies: the unguessable callback URL (``MPESA_CALLBACK_TOKEN`` in the
    path), Safaricom source-IP allowlisting (:func:`is_safaricom_ip`),
    replay detection (:func:`is_replay_attack`) and TLS.

    Because a genuine callback never contains these fields, their absence
    is expected and is accepted - rejecting it would drop 100% of real
    Safaricom traffic. If a request unexpectedly *does* carry a
    password/timestamp pair, a mismatch against the known request-password
    formula is treated as an anomaly and rejected; there is no shared
    callback secret to verify a "match" against, so a match is accepted.

    Returns True to accept, False to reject.
    """
    payload = getattr(request, '_mpesa_callback_body', request.data)
    callback = _get_callback_payload(payload)
    received_password = _get_first_value(callback, 'password', 'Password')
    timestamp = _get_first_value(callback, 'timestamp', 'Timestamp')

    if not received_password or not timestamp:
        logger.debug(
            'M-Pesa callback carries no password/timestamp - expected, '
            'Daraja callbacks are unsigned. Authenticity is enforced by '
            'the callback token, IP allowlist and replay detection.',
        )
        return True

    raw_password = (
        f'{settings.MPESA_SHORTCODE}{settings.MPESA_PASSKEY}{timestamp}'
    )
    expected_password = base64.b64encode(raw_password.encode())
    if hmac.compare_digest(received_password.encode(), expected_password):
        logger.debug(
            'M-Pesa callback carried a password matching the request '
            'formula (unusual but not invalid).',
        )
        return True

    logger.warning(
        'M-Pesa callback carried an unexpected password/timestamp pair '
        'that did not match the request-password formula - rejecting as '
        'anomalous.',
    )
    return False


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

    ip_ranges = _get_safaricom_ip_ranges()
    for ip_range in ip_ranges:
        if request_ip in ipaddress.ip_network(ip_range):
            return True

    logger.warning(
        'M-Pesa callback request rejected from non-Safaricom IP: %s. '
        'Allowed ranges: %s',
        ip_address,
        ip_ranges,
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
    """Real client IP of an inbound M-Pesa callback.

    Delegates to the single canonical resolver
    (:func:`config.utils.get_client_ip`), which trusts only the hop
    Railway's Envoy edge adds (``X-Envoy-External-Address``, else the
    rightmost non-internal ``X-Forwarded-For`` entry, else
    ``REMOTE_ADDR``). Trusting a client-supplied left-hand entry here
    would let anyone present a Safaricom IP to :func:`is_safaricom_ip`.
    """
    return get_client_ip(request)


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
