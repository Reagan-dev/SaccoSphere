import logging

import requests
from django.conf import settings


logger = logging.getLogger('saccosphere.notifications')


class FCMError(Exception):
    def __init__(self, message, response=None, error_code=None):
        super().__init__(message)
        self.message = message
        self.response = response or {}
        self.error_code = error_code

    @property
    def invalid_registration(self):
        return self.error_code in {'InvalidRegistration', 'NotRegistered'}


class FCMPushClient:
    FCM_URL = 'https://fcm.googleapis.com/fcm/send'

    def __init__(self):
        self.server_key = settings.FCM_SERVER_KEY

    def send(self, device_token, title, body, data=None):
        payload = {
            'to': device_token,
            'notification': {
                'title': title,
                'body': body,
            },
            'data': data or {},
        }

        if settings.DEBUG:
            logger.info(
                '[DEBUG MODE] Push notification for token=%s title=%s',
                device_token,
                title,
            )
            return {'success': 1, 'debug': True}

        if not self.server_key:
            raise FCMError('FCM_SERVER_KEY must be configured.')

        headers = {
            'Authorization': f'key={self.server_key}',
            'Content-Type': 'application/json',
        }

        try:
            response = requests.post(
                self.FCM_URL,
                json=payload,
                headers=headers,
                timeout=30,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise FCMError('Failed to send FCM push notification.') from exc

        response_data = response.json()
        if response_data.get('success') == 0:
            error_code = self._get_error_code(response_data)
            raise FCMError(
                'FCM push notification failed.',
                response=response_data,
                error_code=error_code,
            )

        return response_data

    def _get_error_code(self, response_data):
        results = response_data.get('results') or []
        if not results:
            return None

        return results[0].get('error')
