# Production credentials require a signed data-sharing/API agreement with the
# Directorate of the National Registration Bureau. This client is written so
# that flipping IPRS_MOCK=False and supplying real IPRS_API_KEY / IPRS_API_URL
# activates production verification with no further code changes.
import logging
import time

import requests
from django.conf import settings


logger = logging.getLogger('saccosphere.iprs')


class IPRSError(Exception):
    """Raised when the IPRS verification service cannot be reached."""

    pass


class IPRSClient:
    """Client for verifying Kenyan identity details through IPRS."""

    MAX_RETRIES = 2
    TIMEOUT_SECONDS = 8

    def __init__(self):
        self.api_key = settings.IPRS_API_KEY
        self.api_url = settings.IPRS_API_URL
        self.mock = settings.DEBUG or settings.IPRS_MOCK

    def verify_id(self, id_number, date_of_birth=None, full_name=None):
        """
        Verify a national ID number and return a standard response dict.
        """
        if self.mock:
            return {
                'outcome': 'verified',
                'verified': True,
                'id_number': id_number,
                'name': 'Test Citizen',
                'date_of_birth': str(date_of_birth) if date_of_birth else None,
                'iprs_reference': f'MOCK-{id_number}',
                'error': '',
            }

        payload = {'id_number': id_number}
        if date_of_birth:
            payload['date_of_birth'] = str(date_of_birth)
        if full_name:
            payload['name'] = full_name

        headers = {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json',
        }

        for attempt in range(self.MAX_RETRIES + 1):
            try:
                response = requests.post(
                    self.api_url,
                    json=payload,
                    headers=headers,
                    timeout=self.TIMEOUT_SECONDS,
                )
            except (
                requests.ConnectionError,
                requests.Timeout,
            ) as exc:
                if attempt < self.MAX_RETRIES:
                    time.sleep(attempt + 1)
                    continue

                logger.warning('IPRS unavailable after retries: %s', exc)
                return self._unavailable_response(
                    id_number,
                    'IPRS connection or timeout error.',
                )
            except requests.RequestException as exc:
                logger.warning('IPRS request failed: %s', exc)
                return self._unavailable_response(
                    id_number,
                    'IPRS request failed.',
                )

            if not 200 <= response.status_code < 300:
                logger.warning(
                    'IPRS returned non-2xx status: %s.',
                    response.status_code,
                )
                return self._unavailable_response(
                    id_number,
                    f'IPRS returned HTTP {response.status_code}.',
                )

            try:
                data = response.json()
            except ValueError:
                logger.warning('IPRS returned invalid JSON.')
                return self._unavailable_response(
                    id_number,
                    'IPRS returned invalid JSON.',
                )

            return self._standardize_response(
                data,
                id_number,
                date_of_birth=date_of_birth,
                full_name=full_name,
            )

        return self._unavailable_response(id_number, 'IPRS unavailable.')

    def _standardize_response(
        self,
        data,
        id_number,
        date_of_birth=None,
        full_name=None,
    ):
        outcome = self._extract_outcome(data)
        iprs_name = data.get('name') or data.get('full_name')
        iprs_date_of_birth = (
            data.get('date_of_birth')
            or data.get('dob')
            or data.get('birth_date')
        )
        verified_flag = bool(
            data.get('verified') or data.get('is_verified') or data.get('valid')
        )
        name_matches = self._matches_name(full_name, iprs_name)
        dob_matches = self._matches_date(date_of_birth, iprs_date_of_birth)

        if (outcome == 'verified' or verified_flag) and (
            name_matches and dob_matches
        ):
            return {
                'outcome': 'verified',
                'verified': True,
                'id_number': data.get('id_number') or id_number,
                'name': iprs_name,
                'date_of_birth': iprs_date_of_birth,
                'iprs_reference': self._extract_reference(data),
                'error': '',
            }

        error = (
            data.get('error')
            or data.get('message')
            or 'IPRS record did not match submitted details.'
        )

        return {
            'outcome': 'mismatch',
            'verified': False,
            'id_number': data.get('id_number') or id_number,
            'name': iprs_name,
            'date_of_birth': iprs_date_of_birth,
            'iprs_reference': self._extract_reference(data),
            'error': error,
        }

    def _extract_outcome(self, data):
        raw_outcome = (
            data.get('outcome')
            or data.get('status')
            or data.get('result')
            or ''
        )
        normalized = str(raw_outcome).strip().lower().replace(' ', '_')

        if normalized in {'verified', 'matched', 'match', 'success'}:
            return 'verified'

        if normalized in {
            'mismatch',
            'not_found',
            'record_not_found',
            'no_record',
            'failed',
        }:
            return 'mismatch'

        if data.get('record_found') is False:
            return 'mismatch'

        return ''

    def _extract_reference(self, data):
        return (
            data.get('iprs_reference')
            or data.get('reference')
            or data.get('request_id')
        )

    def _matches_name(self, submitted_name, iprs_name):
        if not submitted_name or not iprs_name:
            return True

        return self._normalize_text(submitted_name) == self._normalize_text(
            iprs_name,
        )

    def _matches_date(self, submitted_date, iprs_date):
        if not submitted_date or not iprs_date:
            return True

        return str(submitted_date)[:10] == str(iprs_date)[:10]

    def _normalize_text(self, value):
        return ' '.join(str(value).lower().split())

    def _unavailable_response(self, id_number, error):
        return {
            'outcome': 'unavailable',
            'verified': False,
            'id_number': id_number,
            'name': None,
            'date_of_birth': None,
            'iprs_reference': None,
            'error': error,
        }
