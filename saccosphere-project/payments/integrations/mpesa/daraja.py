import base64

import requests
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone


class DarajaError(Exception):
    def __init__(self, message, response_code=None):
        super().__init__(message)
        self.message = message
        self.response_code = response_code


class DarajaClient:
    SANDBOX_BASE_URL = 'https://sandbox.safaricom.co.ke'
    LIVE_BASE_URL = 'https://api.safaricom.co.ke'

    def __init__(self):
        self.consumer_key = settings.MPESA_CONSUMER_KEY
        self.consumer_secret = settings.MPESA_CONSUMER_SECRET
        self.shortcode = settings.MPESA_SHORTCODE
        self.passkey = settings.MPESA_PASSKEY
        self.environment = settings.MPESA_ENVIRONMENT
        self.callback_base_url = settings.MPESA_CALLBACK_BASE_URL.rstrip('/')
        self.base_url = self._get_base_url()

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
        token = cache.get('mpesa_access_token')
        if token:
            return token

        auth_value = f'{self.consumer_key}:{self.consumer_secret}'
        encoded_auth = base64.b64encode(auth_value.encode()).decode()
        headers = {'Authorization': f'Basic {encoded_auth}'}

        try:
            response = requests.post(
                self._get_auth_url,
                headers=headers,
                timeout=30,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise DarajaError('Failed to get M-Pesa access token.') from exc

        data = response.json()
        token = data.get('access_token')
        if not token:
            raise DarajaError(
                'M-Pesa access token response did not include a token.',
                data.get('errorCode'),
            )

        cache.set('mpesa_access_token', token, timeout=50 * 60)
        return token

    def initiate_stk_push(
        self,
        phone_number,
        amount,
        account_reference,
        description,
        callback_path,
    ):
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
    ):
        token = self.get_access_token()
        payload = {
            'InitiatorName': settings.MPESA_B2C_INITIATOR_NAME,
            'SecurityCredential': settings.MPESA_B2C_SECURITY_CREDENTIAL,
            'CommandID': 'BusinessPayment',
            'Amount': int(amount),
            'PartyA': settings.MPESA_SHORTCODE,
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

        try:
            response = requests.post(
                url,
                json=payload,
                headers=headers,
                timeout=30,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise DarajaError('M-Pesa request failed.') from exc

        return response.json()
