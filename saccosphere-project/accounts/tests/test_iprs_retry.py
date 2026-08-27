"""Tests for IPRS client retry logic with exponential backoff and error classification."""

import logging
import requests
import time
from unittest.mock import patch, Mock
from django.test import TestCase

from accounts.integrations.iprs_client import IPRSClient, IPRSError


class IPRSRetryTestCase(TestCase):
    """Test IPRS client retry behavior with transient vs permanent errors."""

    def setUp(self):
        """Set up test fixtures."""
        self.client = IPRSClient()
        self.client.mock = False
        self.client.api_key = 'test-key'
        self.client.api_url = 'http://test-iprs.example.com'

    def test_transient_5xx_error_retries_with_backoff(self):
        """Test that 5xx errors are retried with exponential backoff."""
        call_count = [0]
        call_times = []

        def mock_post(*args, **kwargs):
            call_count[0] += 1
            call_times.append(time.time())
            response = Mock()
            response.status_code = 503
            response.json.side_effect = ValueError('No JSON')
            return response

        with patch('accounts.integrations.iprs_client.requests.post', side_effect=mock_post):
            start_time = time.time()
            result = self.client.verify_id(
                '12345678',
                correlation_id='test-correlation',
                kyc_submission_id='test-submission',
            )
            end_time = time.time()

        # Should have been called MAX_RETRIES times (3)
        self.assertEqual(call_count[0], 3)

        # Result should be iprs_unavailable
        self.assertEqual(result['outcome'], 'iprs_unavailable')
        self.assertFalse(result['verified'])

        # Check that delays increased (exponential backoff)
        if len(call_times) > 1:
            delay1 = call_times[1] - call_times[0]
            delay2 = call_times[2] - call_times[1]
            # Second delay should be longer than first (exponential)
            self.assertGreater(delay2, delay1)

    def test_permanent_4xx_error_no_retry(self):
        """Test that 4xx errors are not retried and return rejected_by_iprs."""
        call_count = [0]

        def mock_post(*args, **kwargs):
            call_count[0] += 1
            response = Mock()
            response.status_code = 404
            response.json.return_value = {
                'error': 'ID not found',
            }
            return response

        with patch('accounts.integrations.iprs_client.requests.post', side_effect=mock_post):
            result = self.client.verify_id(
                '12345678',
                correlation_id='test-correlation',
                kyc_submission_id='test-submission',
            )

        # Should have been called only once (no retry)
        self.assertEqual(call_count[0], 1)

        # Result should be rejected_by_iprs
        self.assertEqual(result['outcome'], 'rejected_by_iprs')
        self.assertFalse(result['verified'])

    def test_401_unauthorized_no_retry(self):
        """Test that 401 unauthorized is not retried."""
        call_count = [0]

        def mock_post(*args, **kwargs):
            call_count[0] += 1
            response = Mock()
            response.status_code = 401
            response.json.return_value = {
                'error': 'Unauthorized',
            }
            return response

        with patch('accounts.integrations.iprs_client.requests.post', side_effect=mock_post):
            result = self.client.verify_id(
                '12345678',
                correlation_id='test-correlation',
                kyc_submission_id='test-submission',
            )

        self.assertEqual(call_count[0], 1)
        self.assertEqual(result['outcome'], 'rejected_by_iprs')

    def test_422_malformed_no_retry(self):
        """Test that 422 malformed request is not retried."""
        call_count = [0]

        def mock_post(*args, **kwargs):
            call_count[0] += 1
            response = Mock()
            response.status_code = 422
            response.json.return_value = {
                'error': 'Malformed request',
            }
            return response

        with patch('accounts.integrations.iprs_client.requests.post', side_effect=mock_post):
            result = self.client.verify_id(
                '12345678',
                correlation_id='test-correlation',
                kyc_submission_id='test-submission',
            )

        self.assertEqual(call_count[0], 1)
        self.assertEqual(result['outcome'], 'rejected_by_iprs')

    def test_connection_error_retries_with_backoff(self):
        """Test that connection errors are retried with backoff."""
        call_count = [0]

        def mock_post(*args, **kwargs):
            call_count[0] += 1
            raise requests.ConnectionError('Connection failed')

        with patch('accounts.integrations.iprs_client.requests.post', side_effect=mock_post):
            result = self.client.verify_id(
                '12345678',
                correlation_id='test-correlation',
                kyc_submission_id='test-submission',
            )

        # Should have been retried MAX_RETRIES times
        self.assertEqual(call_count[0], 3)

        # Result should be iprs_unavailable
        self.assertEqual(result['outcome'], 'iprs_unavailable')

    def test_timeout_error_retries_with_backoff(self):
        """Test that timeout errors are retried with backoff."""
        call_count = [0]

        def mock_post(*args, **kwargs):
            call_count[0] += 1
            raise requests.Timeout('Request timeout')

        with patch('accounts.integrations.iprs_client.requests.post', side_effect=mock_post):
            result = self.client.verify_id(
                '12345678',
                correlation_id='test-correlation',
                kyc_submission_id='test-submission',
            )

        self.assertEqual(call_count[0], 3)
        self.assertEqual(result['outcome'], 'iprs_unavailable')

    def test_success_on_second_attempt(self):
        """Test that success on second attempt returns verified with no failure trace."""
        call_count = [0]

        def mock_post(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                # First attempt: transient error
                response = Mock()
                response.status_code = 503
                response.json.side_effect = ValueError('No JSON')
                return response
            else:
                # Second attempt: success
                response = Mock()
                response.status_code = 200
                response.json.return_value = {
                    'verified': True,
                    'name': 'Test Citizen',
                    'date_of_birth': '1990-01-01',
                    'id_number': '12345678',
                    'iprs_reference': 'REF-123',
                }
                return response

        with patch('accounts.integrations.iprs_client.requests.post', side_effect=mock_post):
            result = self.client.verify_id(
                '12345678',
                correlation_id='test-correlation',
                kyc_submission_id='test-submission',
            )

        # Should have been called twice (first failed, second succeeded)
        self.assertEqual(call_count[0], 2)

        # Result should be verified
        self.assertEqual(result['outcome'], 'verified')
        self.assertTrue(result['verified'])
        self.assertEqual(result['name'], 'Test Citizen')
        self.assertEqual(result['iprs_reference'], 'REF-123')

    def test_429_rate_limit_retries(self):
        """Test that 429 rate limit is treated as transient and retried."""
        call_count = [0]

        def mock_post(*args, **kwargs):
            call_count[0] += 1
            response = Mock()
            response.status_code = 429
            response.json.side_effect = ValueError('No JSON')
            return response

        with patch('accounts.integrations.iprs_client.requests.post', side_effect=mock_post):
            result = self.client.verify_id(
                '12345678',
                correlation_id='test-correlation',
                kyc_submission_id='test-submission',
            )

        # Should have been retried
        self.assertEqual(call_count[0], 3)
        self.assertEqual(result['outcome'], 'iprs_unavailable')

    def test_408_request_timeout_retries(self):
        """Test that 408 request timeout is treated as transient and retried."""
        call_count = [0]

        def mock_post(*args, **kwargs):
            call_count[0] += 1
            response = Mock()
            response.status_code = 408
            response.json.side_effect = ValueError('No JSON')
            return response

        with patch('accounts.integrations.iprs_client.requests.post', side_effect=mock_post):
            result = self.client.verify_id(
                '12345678',
                correlation_id='test-correlation',
                kyc_submission_id='test-submission',
            )

        self.assertEqual(call_count[0], 3)
        self.assertEqual(result['outcome'], 'iprs_unavailable')

    def test_is_transient_error_classification(self):
        """Test the _is_transient_error classification logic."""
        # Transient HTTP status codes
        for code in [408, 429, 500, 502, 503, 504]:
            self.assertTrue(
                self.client._is_transient_error(code),
                f'Status code {code} should be transient',
            )

        # Permanent HTTP status codes
        for code in [400, 401, 403, 404, 422]:
            self.assertFalse(
                self.client._is_transient_error(code),
                f'Status code {code} should be permanent',
            )

        # Connection errors are transient
        self.assertTrue(
            self.client._is_transient_error(
                None, exception=requests.ConnectionError()
            )
        )
        self.assertTrue(
            self.client._is_transient_error(None, exception=requests.Timeout())
        )

        # Unknown 5xx codes are transient
        self.assertTrue(self.client._is_transient_error(599))

        # Unknown 4xx codes are permanent
        self.assertFalse(self.client._is_transient_error(499))

    def test_mismatch_outcome_not_retried(self):
        """Test that mismatch outcome from IPRS is not retried."""
        call_count = [0]

        def mock_post(*args, **kwargs):
            call_count[0] += 1
            response = Mock()
            response.status_code = 200
            response.json.return_value = {
                'verified': False,
                'name': 'Different Name',
                'date_of_birth': '1990-01-01',
                'id_number': '12345678',
            }
            return response

        with patch('accounts.integrations.iprs_client.requests.post', side_effect=mock_post):
            result = self.client.verify_id(
                '12345678',
                date_of_birth='1990-01-01',
                full_name='Test User',
                correlation_id='test-correlation',
                kyc_submission_id='test-submission',
            )

        # Should have been called only once (no retry for mismatch)
        self.assertEqual(call_count[0], 1)

        # Result should be mismatch
        self.assertEqual(result['outcome'], 'mismatch')
        self.assertFalse(result['verified'])

    def test_json_decode_error_treated_as_transient(self):
        """Test that JSON decode errors are treated as transient and retried."""
        call_count = [0]

        def mock_post(*args, **kwargs):
            call_count[0] += 1
            response = Mock()
            response.status_code = 200
            response.json.side_effect = ValueError('Invalid JSON')
            return response

        with patch('accounts.integrations.iprs_client.requests.post', side_effect=mock_post):
            result = self.client.verify_id(
                '12345678',
                correlation_id='test-correlation',
                kyc_submission_id='test-submission',
            )

        # Should have been retried
        self.assertEqual(call_count[0], 3)
        self.assertEqual(result['outcome'], 'iprs_unavailable')
