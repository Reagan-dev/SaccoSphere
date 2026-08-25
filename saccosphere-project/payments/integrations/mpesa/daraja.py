import base64
import logging

import requests
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger('saccosphere.payments')


class DarajaError(Exception):
    def __init__(self, message, response_code=None):
        super().__init__(message)
        self.message = message
        self.response_code = response_code


def format_phone_for_daraja(e164_number: str) -> str:
    """Format an E.164 phone number for Daraja API.
    
    Daraja expects phone numbers in 2547XXXXXXXX format (no leading +).
    This function takes an already-normalized E.164 number and strips
    the leading + sign.
    
    Args:
        e164_number: Phone number in E.164 format (e.g., +254712345678)
    
    Returns:
        str: Phone number in Daraja format (e.g., 254712345678)
    
    Raises:
        ValueError: If the input is not a valid E.164 Kenyan number
    """
    if not e164_number or not isinstance(e164_number, str):
        raise ValueError('Phone number must be a non-empty string')
    
    if not e164_number.startswith('+254'):
        raise ValueError(
            f'Expected E.164 format starting with +254, got: {e164_number}'
        )
    
    # Strip the leading + to get Daraja format
    return e164_number[1:]


class DarajaClient:
    SANDBOX_BASE_URL = 'https://sandbox.safaricom.co.ke'
    LIVE_BASE_URL = 'https://api.safaricom.co.ke'

    def __init__(
        self,
        consumer_key=None,
        consumer_secret=None,
        shortcode=None,
        passkey=None,
        environment=None,
        callback_base_url=None,
    ):
        """Initialize DarajaClient with optional SACCO-specific credentials.
        
        Args:
            consumer_key: Daraja consumer key (defaults to settings.MPESA_CONSUMER_KEY)
            consumer_secret: Daraja consumer secret (defaults to settings.MPESA_CONSUMER_SECRET)
            shortcode: M-Pesa shortcode (defaults to settings.MPESA_SHORTCODE)
            passkey: STK push passkey (defaults to settings.MPESA_PASSKEY)
            environment: Daraja environment (defaults to settings.MPESA_ENVIRONMENT)
            callback_base_url: Base URL for callbacks (defaults to settings.MPESA_CALLBACK_BASE_URL)
        """
        self.consumer_key = consumer_key or settings.MPESA_CONSUMER_KEY
        self.consumer_secret = consumer_secret or settings.MPESA_CONSUMER_SECRET
        self.shortcode = shortcode or settings.MPESA_SHORTCODE
        self.passkey = passkey or settings.MPESA_PASSKEY
        self.environment = environment or settings.MPESA_ENVIRONMENT
        self.callback_base_url = (callback_base_url or settings.MPESA_CALLBACK_BASE_URL).rstrip('/')
        self.base_url = self._get_base_url()
        
        # Generate a unique cache key for this credential set
        self._cache_key_prefix = f'mpesa_access_token:{self.consumer_key}'

    @property
    def _get_auth_url(self):
        return (
            f'{self.base_url}/oauth/v1/generate'
            '?grant_type=client_credentials'
        )

    @property
    def _get_stk_url(self):
        return f'{self.base_url}/mpesa/stkpush/v1/processrequest'

    @property
    def _get_stk_query_url(self):
        return f'{self.base_url}/mpesa/stkpushquery/v1/query'

    @property
    def _get_b2c_url(self):
        return f'{self.base_url}/mpesa/b2c/v1/paymentrequest'

    def get_access_token(self):
        """Get OAuth access token, cached per credential set."""
        token = cache.get(self._cache_key_prefix)
        if token:
            logger.debug(
                'M-Pesa access token retrieved from cache for consumer_key=%s',
                self.consumer_key[:8] + '...'  # Log partial key for security
            )
            return token

        self._require_settings(
            'MPESA_CONSUMER_KEY',
            'MPESA_CONSUMER_SECRET',
        )

        auth_value = f'{self.consumer_key}:{self.consumer_secret}'
        encoded_auth = base64.b64encode(auth_value.encode()).decode()
        headers = {'Authorization': f'Basic {encoded_auth}'}

        logger.debug(
            'Requesting new M-Pesa access token from: %s for consumer_key=%s',
            self._get_auth_url,
            self.consumer_key[:8] + '...',
        )

        try:
            response = requests.get(
                self._get_auth_url,
                headers=headers,
                timeout=30,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            logger.error(
                'Failed to get M-Pesa access token: %s',
                exc,
                exc_info=True,
            )
            raise DarajaError('Failed to get M-Pesa access token.') from exc

        data = self._parse_json_response(
            response,
            'M-Pesa access token response was not valid JSON.',
        )
        token = data.get('access_token')
        if not token:
            logger.error(
                'M-Pesa access token response did not include token: %s',
                data,
            )
            raise DarajaError(
                'M-Pesa access token response did not include a token.',
                data.get('errorCode'),
            )

        # Cache token per credential set for 50 minutes
        cache.set(self._cache_key_prefix, token, timeout=50 * 60)
        logger.debug(
            'M-Pesa access token cached for 50 minutes for consumer_key=%s',
            self.consumer_key[:8] + '...'
        )
        return token

    def initiate_stk_push(
        self,
        phone_number,
        amount,
        account_reference,
        description,
        callback_path,
    ):
        self._require_settings(
            'MPESA_CONSUMER_KEY',
            'MPESA_CONSUMER_SECRET',
            'MPESA_SHORTCODE',
            'MPESA_PASSKEY',
            'MPESA_CALLBACK_BASE_URL',
        )
        token = self.get_access_token()
        timestamp = self._generate_timestamp()
        password = self._generate_password(timestamp)
        callback_url = self._build_callback_url(callback_path)
        payload = {
            'BusinessShortCode': self.shortcode,
            'Password': password,
            'Timestamp': timestamp,
            'TransactionType': 'CustomerPayBillOnline',
            'Amount': int(amount),
            'PartyA': phone_number,
            'PartyB': self.shortcode,
            'PhoneNumber': phone_number,
            'CallBackURL': callback_url,
            'AccountReference': account_reference,
            'TransactionDesc': description,
        }

        logger.debug(
            'Initiating M-Pesa STK push: phone=%s, amount=%s, '
            'callback_url=%s, timestamp=%s',
            phone_number,
            amount,
            callback_url,
            timestamp,
        )

        data = self._post(self._get_stk_url, token, payload)
        response_code = data.get('ResponseCode')
        if response_code != '0':
            raise DarajaError(
                data.get('errorMessage')
                or data.get('ResponseDescription')
                or 'M-Pesa STK push failed.',
                response_code,
            )

        return data

    def query_stk_status(self, checkout_request_id):
        self._require_settings(
            'MPESA_CONSUMER_KEY',
            'MPESA_CONSUMER_SECRET',
            'MPESA_SHORTCODE',
            'MPESA_PASSKEY',
        )
        token = self.get_access_token()
        timestamp = self._generate_timestamp()
        password = self._generate_password(timestamp)
        payload = {
            'BusinessShortCode': self.shortcode,
            'Password': password,
            'Timestamp': timestamp,
            'CheckoutRequestID': checkout_request_id,
        }

        return self._post(self._get_stk_query_url, token, payload)

    def initiate_b2c(
        self,
        phone_number,
        amount,
        occasion,
        remarks,
        result_url,
        timeout_url,
        initiator_name=None,
        security_credential=None,
    ):
        """Initiate B2C disbursement with optional SACCO-specific credentials.
        
        Args:
            phone_number: Recipient phone number in Daraja format (2547...)
            amount: Disbursement amount
            occasion: Transaction occasion description
            remarks: Transaction remarks
            result_url: Callback URL for successful results
            timeout_url: Callback URL for timeout results
            initiator_name: B2C initiator name (defaults to settings.MPESA_B2C_INITIATOR_NAME)
            security_credential: B2C security credential (defaults to settings.MPESA_B2C_SECURITY_CREDENTIAL)
        """
        initiator_name = initiator_name or settings.MPESA_B2C_INITIATOR_NAME
        security_credential = security_credential or settings.MPESA_B2C_SECURITY_CREDENTIAL
        
        if not initiator_name or not security_credential:
            raise DarajaError(
                'B2C initiator name and security credential are required.'
            )
        
        token = self.get_access_token()
        payload = {
            'InitiatorName': initiator_name,
            'SecurityCredential': security_credential,
            'CommandID': 'BusinessPayment',
            'Amount': int(amount),
            'PartyA': self.shortcode,
            'PartyB': phone_number,
            'Remarks': remarks[:100],
            'QueueTimeOutURL': timeout_url,
            'ResultURL': result_url,
            'Occasion': occasion[:100],
        }

        data = self._post(self._get_b2c_url, token, payload)
        response_code = str(data.get('ResponseCode'))
        if response_code != '0':
            raise DarajaError(
                data.get('errorMessage')
                or data.get('ResponseDescription')
                or 'M-Pesa B2C disbursement failed.',
                response_code,
            )

        return data

    def _build_callback_url(self, callback_path):
        if callback_path.startswith('/'):
            path = callback_path
        else:
            path = f'/{callback_path}'

        return f'{self.callback_base_url}{path}'

    def _generate_password(self, timestamp):
        raw_password = f'{self.shortcode}{self.passkey}{timestamp}'
        return base64.b64encode(raw_password.encode()).decode()

    def _generate_timestamp(self):
        return timezone.now().strftime('%Y%m%d%H%M%S')

    def _get_base_url(self):
        if str(self.environment).lower() == 'live':
            return self.LIVE_BASE_URL

        return self.SANDBOX_BASE_URL

    def _post(self, url, token, payload):
        headers = {
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json',
        }

        logger.debug(
            'M-Pesa API request: POST %s, payload keys: %s',
            url,
            list(payload.keys()),
        )

        try:
            response = requests.post(
                url,
                json=payload,
                headers=headers,
                timeout=30,
            )
            response.raise_for_status()
        except requests.exceptions.Timeout as exc:
            logger.error(
                'M-Pesa API timeout: %s',
                exc,
                exc_info=True,
            )
            raise DarajaError(
                'M-Pesa request timed out. Please try again.',
            ) from exc
        except requests.exceptions.ConnectionError as exc:
            logger.error(
                'M-Pesa connection error: %s',
                exc,
                exc_info=True,
            )
            raise DarajaError(
                'Failed to connect to M-Pesa API. Check your internet connection.',
            ) from exc
        except requests.exceptions.HTTPError as exc:
            response_text = getattr(exc.response, 'text', '')
            logger.error(
                'M-Pesa HTTP error %s at %s: %s',
                exc.response.status_code,
                url,
                response_text,
                exc_info=True,
            )
            raise DarajaError(
                f'M-Pesa API returned error {exc.response.status_code}.',
                exc.response.status_code,
            ) from exc
        except requests.RequestException as exc:
            logger.error(
                'M-Pesa request error: %s',
                exc,
                exc_info=True,
            )
            raise DarajaError('M-Pesa request failed.') from exc

        return self._parse_json_response(
            response,
            'M-Pesa response was not valid JSON.',
        )

    def _parse_json_response(self, response, message):
        try:
            return response.json()
        except ValueError as exc:
            raise DarajaError(message) from exc

    def _require_settings(self, *setting_names):
        missing = [
            setting_name
            for setting_name in setting_names
            if not str(getattr(settings, setting_name, '')).strip()
        ]
        if missing:
            raise DarajaError(
                'M-Pesa configuration is missing: ' + ', '.join(missing)
            )
