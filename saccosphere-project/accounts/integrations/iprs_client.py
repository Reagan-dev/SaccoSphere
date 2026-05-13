# NOTE: Real IPRS requires formal Kenyan government API partner agreement.
import logging

import requests
from django.conf import settings


logger = logging.getLogger('saccosphere.iprs')


class IPRSError(Exception):
    """Raised when the IPRS verification service cannot be reached."""

    pass


class IPRSClient:
    """Client for verifying Kenyan identity details through IPRS."""

    def __init__(self):
        self.api_key = settings.IPRS_API_KEY
        self.api_url = settings.IPRS_API_URL
        self.mock = settings.DEBUG or settings.IPRS_MOCK

    def verify_id(self, id_number, date_of_birth=None):
        """
        Verify a national ID number and return a standard response dict.
        """
        if self.mock:
            return {
                'verified': True,
                'id_number': id_number,
                'name': 'Test Citizen',
                'iprs_reference': f'MOCK-{id_number}',
            }

        payload = {'id_number': id_number}
        if date_of_birth:
            payload['date_of_birth'] = str(date_of_birth)

        headers = {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json',
        }

        try:
            response = requests.post(
                self.api_url,
                json=payload,
                headers=headers,
                timeout=10,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            logger.exception('IPRS request failed.')
            raise IPRSError('IPRS request failed.') from exc

        try:
            data = response.json()
        except ValueError as exc:
            raise IPRSError('IPRS returned invalid JSON.') from exc

        return self._standardize_response(data, id_number)

    def _standardize_response(self, data, id_number):
        verified = bool(
            data.get('verified')
            or data.get('is_verified')
            or data.get('valid')
        )

        return {
            'verified': verified,
            'id_number': data.get('id_number') or id_number,
            'name': data.get('name') or data.get('full_name'),
            'iprs_reference': (
                data.get('iprs_reference')
                or data.get('reference')
                or data.get('request_id')
            ),
            'error': data.get('error') or data.get('message'),
        }
