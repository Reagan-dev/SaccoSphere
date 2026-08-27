# Production credentials require a signed data-sharing/API agreement with the
# Directorate of the National Registration Bureau. This client is written so
# that flipping IPRS_MOCK=False and supplying real IPRS_API_KEY / IPRS_API_URL
# activates production verification with no further code changes.
import logging
import random

import requests
from django.conf import settings
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential_jitter,
    before_sleep_log,
    retry_if_exception_type,
)
from config.utils import sanitize_pii


logger = logging.getLogger('saccosphere.iprs')


class IPRSError(Exception):
    """Raised when the IPRS verification service cannot be reached."""

    pass


class TransientIPRSError(IPRSError):
    """Raised for transient IPRS errors that should be retried."""

    pass


class IPRSClient:
    """Client for verifying Kenyan identity details through IPRS."""

    MAX_RETRIES = 3
    TIMEOUT_SECONDS = 8
    TRANSIENT_HTTP_STATUS_CODES = {408, 429, 500, 502, 503, 504}
    PERMANENT_HTTP_STATUS_CODES = {400, 401, 403, 404, 422}

    def __init__(self):
        self.api_key = settings.IPRS_API_KEY
        self.api_url = settings.IPRS_API_URL
        self.mock = settings.DEBUG or settings.IPRS_MOCK

    def _is_transient_error(self, status_code, exception=None):
        """
        Determine if an error is transient (worth retrying) or permanent.

        Args:
            status_code: HTTP status code (if available)
            exception: The exception that occurred (if available)

        Returns:
            bool: True if the error is transient, False if permanent
        """
        if status_code in self.TRANSIENT_HTTP_STATUS_CODES:
            return True
        if status_code in self.PERMANENT_HTTP_STATUS_CODES:
            return False
        if exception:
            # Connection errors and timeouts are transient
            if isinstance(exception, (requests.ConnectionError, requests.Timeout)):
                return True
        # Unknown status codes are treated as transient for safety
        return status_code is None or status_code >= 500

    def _make_iprs_request(self, payload, headers, correlation_id=None, kyc_submission_id=None):
        """
        Make a single IPRS API request.

        Args:
            payload: Request payload
            headers: Request headers
            correlation_id: Correlation ID for tracing
            kyc_submission_id: KYC submission UUID for tracing

        Returns:
            tuple: (response_dict, is_transient_error)
        """
        sanitized_id = sanitize_pii(payload.get('id_number', ''))
        log_context = {
            'correlation_id': correlation_id or '-',
            'kyc_submission_id': kyc_submission_id or '-',
            'id_number_ref': sanitized_id,
            'step': 'iprs_verification',
        }

        try:
            response = requests.post(
                self.api_url,
                json=payload,
                headers=headers,
                timeout=self.TIMEOUT_SECONDS,
            )
        except (requests.ConnectionError, requests.Timeout) as exc:
            logger.warning(
                'IPRS connection error',
                extra={
                    **log_context,
                    'error_type': 'connection_error',
                    'error_class': exc.__class__.__name__,
                    'outcome': 'iprs_unavailable',
                },
            )
            return None, True  # Transient error
        except requests.RequestException as exc:
            logger.warning(
                'IPRS request error',
                extra={
                    **log_context,
                    'error_type': 'request_error',
                    'error_class': exc.__class__.__name__,
                    'outcome': 'iprs_unavailable',
                },
            )
            return None, False  # Permanent error

        if not 200 <= response.status_code < 300:
            is_transient = self._is_transient_error(response.status_code)
            error_type = 'transient_http_error' if is_transient else 'permanent_http_error'
            outcome = 'iprs_unavailable' if is_transient else 'rejected_by_iprs'

            logger.warning(
                'IPRS HTTP error',
                extra={
                    **log_context,
                    'error_type': error_type,
                    'http_status': response.status_code,
                    'outcome': outcome,
                },
            )
            return None, is_transient

        try:
            data = response.json()
        except ValueError:
            logger.warning(
                'IPRS JSON decode error',
                extra={
                    **log_context,
                    'error_type': 'json_decode_error',
                    'outcome': 'iprs_unavailable',
                },
            )
            return None, True  # Treat as transient

        return data, False
    @retry(
        stop=stop_after_attempt(MAX_RETRIES),
        wait=wait_exponential_jitter(
            initial=1,
            max=32,
            jitter=1,
        ),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
        retry=retry_if_exception_type(TransientIPRSError),
    )
    def _verify_id_with_retry(
        self,
        payload,
        headers,
        correlation_id=None,
        kyc_submission_id=None,
    ):
        """
        Execute IPRS verification with exponential backoff retry for transient errors.

        Args:
            payload: Request payload
            headers: Request headers
            correlation_id: Correlation ID for tracing
            kyc_submission_id: KYC submission UUID for tracing

        Returns:
            dict: IPRS response data

        Raises:
            IPRSError: If all retries are exhausted for transient errors
        """
        sanitized_id = sanitize_pii(payload.get('id_number', ''))
        log_context = {
            'correlation_id': correlation_id or '-',
            'kyc_submission_id': kyc_submission_id or '-',
            'id_number_ref': sanitized_id,
            'step': 'iprs_verification',
        }

        response_data, is_transient = self._make_iprs_request(
            payload, headers, correlation_id, kyc_submission_id
        )

        if response_data is None:
            if is_transient:
                # Raise to trigger retry
                raise TransientIPRSError('Transient IPRS error, retrying')
            else:
                # Permanent error, raise to avoid retry
                raise IPRSError('Permanent IPRS error, no retry')

        return response_data

    def verify_id(
        self,
        id_number,
        date_of_birth=None,
        full_name=None,
        correlation_id=None,
        kyc_submission_id=None,
    ):
        """
        Verify a national ID number and return a standard response dict.

        Args:
            id_number: The national ID number to verify
            date_of_birth: Optional date of birth for verification
            full_name: Optional full name for verification
            correlation_id: Correlation ID for tracing the request
            kyc_submission_id: KYC submission UUID for tracing
        """
        sanitized_id = sanitize_pii(id_number)
        log_context = {
            'correlation_id': correlation_id or '-',
            'kyc_submission_id': kyc_submission_id or '-',
            'id_number_ref': sanitized_id,
            'step': 'iprs_verification',
        }

        if self.mock:
            logger.info(
                'IPRS mock verification',
                extra={**log_context, 'outcome': 'verified'},
            )
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

        try:
            data = self._verify_id_with_retry(
                payload, headers, correlation_id, kyc_submission_id
            )
        except TransientIPRSError:
            logger.warning(
                'IPRS unavailable after retries',
                extra={
                    **log_context,
                    'error_type': 'max_retries_exceeded',
                    'outcome': 'iprs_unavailable',
                },
            )
            return self._unavailable_response(
                id_number,
                'IPRS unavailable after retries.',
            )
        except IPRSError as exc:
            # Permanent error, return rejected response
            logger.warning(
                'IPRS permanent error',
                extra={
                    **log_context,
                    'error_type': 'permanent_error',
                    'outcome': 'rejected_by_iprs',
                },
            )
            return self._rejected_response(
                id_number,
                str(exc),
            )

        # If the response already has a final outcome (rejected/unavailable),
        # return it directly without standardization
        if data.get('outcome') in {'rejected_by_iprs', 'iprs_unavailable'}:
            logger.info(
                'IPRS verification completed',
                extra={
                    **log_context,
                    'outcome': data.get('outcome'),
                    'verified': data.get('verified'),
                },
            )
            return data

        result = self._standardize_response(
            data,
            id_number,
            date_of_birth=date_of_birth,
            full_name=full_name,
        )

        logger.info(
            'IPRS verification completed',
            extra={
                **log_context,
                'outcome': result.get('outcome'),
                'verified': result.get('verified'),
            },
        )

        return result

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

    def _rejected_response(self, id_number, error):
        """Return a response for permanent IPRS rejection."""
        return {
            'outcome': 'rejected_by_iprs',
            'verified': False,
            'id_number': id_number,
            'name': None,
            'date_of_birth': None,
            'iprs_reference': None,
            'error': error,
        }

    def _unavailable_response(self, id_number, error):
        """Return a response for transient IPRS unavailability."""
        return {
            'outcome': 'iprs_unavailable',
            'verified': False,
            'id_number': id_number,
            'name': None,
            'date_of_birth': None,
            'iprs_reference': None,
            'error': error,
        }
