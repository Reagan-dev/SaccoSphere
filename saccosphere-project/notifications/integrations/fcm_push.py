import json
import logging
from datetime import datetime, timezone as dt_timezone

import requests
from django.conf import settings
from django.core.cache import cache
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2 import service_account


logger = logging.getLogger('saccosphere.notifications')

FCM_SCOPES = ['https://www.googleapis.com/auth/firebase.messaging']

# FCM HTTP v1 error statuses (google.firebase.fcm.v1.FcmErrorCode) that mean
# the registration token itself is dead and should never be retried.
_UNREGISTERED_STATUSES = {'UNREGISTERED'}

# Statuses that indicate a systemic/configuration problem rather than a
# transient gateway hiccup - retrying will not help, and every push in the
# batch will fail the same way, so callers should stop and surface this
# loudly instead of burning through retries per device token.
_PERMANENT_FAILURE_STATUSES = {
    'INVALID_ARGUMENT',
    'SENDER_ID_MISMATCH',
    'UNAUTHENTICATED',
    'PERMISSION_DENIED',
    'UNCONFIGURED',
}

# Cache key/margin for the cached OAuth2 access token used to authenticate
# against FCM v1. Access tokens are valid for ~1 hour; refreshing early
# avoids sending a request with a token that expires mid-flight.
_ACCESS_TOKEN_CACHE_KEY = 'notifications:fcm:access_token'
_TOKEN_REFRESH_MARGIN_SECONDS = 60


class FCMError(Exception):
    """Raised when a push notification could not be delivered via FCM."""

    def __init__(
        self,
        message,
        response=None,
        status_code=None,
        error_status=None,
    ):
        super().__init__(message)
        self.message = message
        self.response = response or {}
        self.status_code = status_code
        self.error_status = error_status

    @property
    def invalid_registration(self):
        """Whether this device token is dead and should be deactivated."""
        return self.error_status in _UNREGISTERED_STATUSES

    @property
    def is_permanent(self):
        """Whether retrying is pointless (bad config, not a blip)."""
        return self.error_status in _PERMANENT_FAILURE_STATUSES


class FCMPushClient:
    """Sends push notifications through the FCM HTTP v1 API.

    Authenticates with a service-account key exchanged for a short-lived
    OAuth2 access token (cached between sends), as required by v1 - the
    legacy server-key API this replaced was shut down by Google in 2024.
    """

    FCM_URL_TEMPLATE = (
        'https://fcm.googleapis.com/v1/projects/{project_id}/messages:send'
    )

    def __init__(self):
        self.project_id = settings.FCM_PROJECT_ID
        self._credentials_info = None

    @property
    def credentials_info(self):
        """Return the parsed service-account key, or None if unconfigured."""
        if self._credentials_info is not None:
            return self._credentials_info

        raw_credentials = settings.FCM_CREDENTIALS_JSON
        if not raw_credentials:
            return None

        try:
            self._credentials_info = json.loads(raw_credentials)
        except (TypeError, ValueError) as exc:
            raise FCMError(
                'FCM_CREDENTIALS_JSON is not valid JSON.',
                error_status='UNCONFIGURED',
            ) from exc

        return self._credentials_info

    def send(self, device_token, title, body, data=None):
        if settings.DEBUG:
            logger.info(
                '[DEBUG MODE] Push notification for token=%s title=%s',
                device_token,
                title,
            )
            return {'success': 1, 'debug': True}

        if not self.project_id or not self.credentials_info:
            raise FCMError(
                'FCM_PROJECT_ID and FCM_CREDENTIALS_JSON must be configured.',
                error_status='UNCONFIGURED',
            )

        access_token = self._get_access_token()
        payload = {
            'message': {
                'token': device_token,
                'notification': {
                    'title': title,
                    'body': body,
                },
                'data': {
                    str(key): str(value)
                    for key, value in (data or {}).items()
                },
            },
        }
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json; UTF-8',
        }
        url = self.FCM_URL_TEMPLATE.format(project_id=self.project_id)

        try:
            response = requests.post(
                url,
                json=payload,
                headers=headers,
                timeout=30,
            )
        except requests.RequestException as exc:
            raise FCMError(
                'Failed to reach FCM.',
                error_status='UNAVAILABLE',
            ) from exc

        if response.status_code == 200:
            return response.json()

        raise self._build_error(response)

    def _get_access_token(self):
        """Return a cached OAuth2 access token, refreshing it if needed."""
        cached_token = cache.get(_ACCESS_TOKEN_CACHE_KEY)
        if cached_token:
            return cached_token

        credentials = service_account.Credentials.from_service_account_info(
            self.credentials_info,
            scopes=FCM_SCOPES,
        )

        try:
            credentials.refresh(GoogleAuthRequest())
        except Exception as exc:
            raise FCMError(
                'Failed to obtain an FCM access token.',
                error_status='UNAUTHENTICATED',
            ) from exc

        ttl_seconds = 3300  # 55 minutes; tokens are valid ~1 hour.
        if credentials.expiry is not None:
            now = datetime.now(dt_timezone.utc)
            expiry = credentials.expiry
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=dt_timezone.utc)
            remaining = int((expiry - now).total_seconds())
            ttl_seconds = max(
                remaining - _TOKEN_REFRESH_MARGIN_SECONDS,
                60,
            )

        cache.set(
            _ACCESS_TOKEN_CACHE_KEY,
            credentials.token,
            timeout=ttl_seconds,
        )
        return credentials.token

    def _build_error(self, response):
        response_data = self._safe_json(response)
        error_body = response_data.get('error') or {}
        error_status = error_body.get('status')

        for detail in error_body.get('details') or []:
            fcm_error_code = detail.get('errorCode')
            if fcm_error_code:
                error_status = fcm_error_code
                break

        message = error_body.get('message') or 'FCM push notification failed.'
        return FCMError(
            message,
            response=response_data,
            status_code=response.status_code,
            error_status=error_status,
        )

    @staticmethod
    def _safe_json(response):
        try:
            return response.json()
        except ValueError:
            return {}
