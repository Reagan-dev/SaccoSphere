"""Unit tests for IPRS client with mocked HTTP layer."""

from unittest.mock import Mock
from django.test import TestCase, override_settings
from requests.exceptions import ConnectionError, Timeout, RequestException

from accounts.integrations.iprs_client import (
    IPRSClient,
    IPRSError,
    TransientIPRSError,
)


class IPRSClientTestCase(TestCase):
    """Test IPRS client with mocked HTTP layer."""

    def setUp(self):
        """Set up IPRS client instance."""
        self.client = IPRSClient()

    @override_settings(IPRS_MOCK=True)
    def test_mock_mode_returns_success(self):
        """Test that mock mode returns success without HTTP call."""
        client = IPRSClient()
        result = client.verify_id('12345678')

        self.assertTrue(result['verified'])
        self.assertEqual(result['outcome'], 'verified')
        self.assertEqual(result['name'], 'Test Citizen')
        self.assertIn('MOCK-', result['iprs_reference'])

    def test_is_transient_error_4xx_permanent(self):
        """Test that 4xx errors are considered permanent."""
        for status_code in [400, 401, 403, 404, 422]:
            is_transient = self.client._is_transient_error(status_code)
            self.assertFalse(is_transient, f"Status {status_code} should be permanent")

    def test_is_transient_error_5xx_transient(self):
        """Test that 5xx errors are considered transient."""
        for status_code in [500, 502, 503, 504]:
            is_transient = self.client._is_transient_error(status_code)
            self.assertTrue(is_transient, f"Status {status_code} should be transient")

    def test_is_transient_error_408_transient(self):
        """Test that 408 timeout is considered transient."""
        is_transient = self.client._is_transient_error(408)
        self.assertTrue(is_transient)

    def test_is_transient_error_429_transient(self):
        """Test that 429 rate limit is considered transient."""
        is_transient = self.client._is_transient_error(429)
        self.assertTrue(is_transient)

    def test_is_transient_error_connection_error(self):
        """Test that connection errors are considered transient."""
        is_transient = self.client._is_transient_error(None, ConnectionError())
        self.assertTrue(is_transient)

    def test_is_transient_error_timeout(self):
        """Test that timeout errors are considered transient."""
        is_transient = self.client._is_transient_error(None, Timeout())
        self.assertTrue(is_transient)

    def test_is_transient_error_unknown_status_transient(self):
        """Test that unknown status codes >= 500 are treated as transient."""
        is_transient = self.client._is_transient_error(599)
        self.assertTrue(is_transient)

    def test_is_transient_error_unknown_4xx_permanent(self):
        """Test that unknown 4xx status codes are treated as permanent."""
        is_transient = self.client._is_transient_error(418)
        self.assertFalse(is_transient)

    def test_extract_outcome_variations(self):
        """Test outcome extraction handles various formats."""
        test_cases = [
            ({'outcome': 'verified'}, 'verified'),
            ({'status': 'matched'}, 'verified'),
            ({'result': 'match'}, 'verified'),
            ({'outcome': 'mismatch'}, 'mismatch'),
            ({'status': 'not_found'}, 'mismatch'),
            ({'result': 'failed'}, 'mismatch'),
            ({'record_found': False}, 'mismatch'),
            ({'outcome': 'unknown'}, ''),
        ]

        for data, expected in test_cases:
            result = self.client._extract_outcome(data)
            self.assertEqual(result, expected, f"Failed for data: {data}")

    def test_normalize_text(self):
        """Test text normalization."""
        test_cases = [
            ('Test Name', 'test name'),
            ('  Test  Name  ', 'test name'),
            ('TEST NAME', 'test name'),
            ('Test\nName', 'test name'),
        ]

        for input_text, expected in test_cases:
            result = self.client._normalize_text(input_text)
            self.assertEqual(result, expected)

    def test_matches_name(self):
        """Test name matching logic."""
        self.assertTrue(self.client._matches_name('Test Name', 'Test Name'))
        self.assertTrue(self.client._matches_name('Test Name', '  Test  Name  '))
        self.assertTrue(self.client._matches_name('Test Name', 'TEST NAME'))
        self.assertTrue(self.client._matches_name(None, 'Test Name'))
        self.assertTrue(self.client._matches_name('Test Name', None))
        self.assertFalse(self.client._matches_name('Test Name', 'Different Name'))

    def test_matches_date(self):
        """Test date matching logic."""
        self.assertTrue(self.client._matches_date('1990-01-01', '1990-01-01'))
        self.assertTrue(self.client._matches_date('1990-01-01T00:00:00', '1990-01-01'))
        self.assertTrue(self.client._matches_date(None, '1990-01-01'))
        self.assertTrue(self.client._matches_date('1990-01-01', None))
        self.assertFalse(self.client._matches_date('1990-01-01', '1990-01-02'))

    def test_standardize_response_verified(self):
        """Test response standardization for verified outcome."""
        data = {
            'outcome': 'verified',
            'verified': True,
            'id_number': '12345678',
            'name': 'Test Citizen',
            'date_of_birth': '1990-01-01',
            'iprs_reference': 'REF123',
        }
        result = self.client._standardize_response(
            data,
            '12345678',
            date_of_birth='1990-01-01',
            full_name='Test Citizen',
        )
        self.assertTrue(result['verified'])
        self.assertEqual(result['outcome'], 'verified')
        self.assertEqual(result['name'], 'Test Citizen')

    def test_standardize_response_mismatch(self):
        """Test response standardization for mismatch outcome."""
        data = {
            'outcome': 'mismatch',
            'verified': False,
            'id_number': '12345678',
            'name': 'Different Name',
            'date_of_birth': '1990-01-01',
        }
        result = self.client._standardize_response(
            data,
            '12345678',
            date_of_birth='1990-01-01',
            full_name='Test Citizen',
        )
        self.assertFalse(result['verified'])
        self.assertEqual(result['outcome'], 'mismatch')

    def test_extract_reference(self):
        """Test reference extraction from various field names."""
        test_cases = [
            ({'iprs_reference': 'REF123'}, 'REF123'),
            ({'reference': 'REF456'}, 'REF456'),
            ({'request_id': 'REF789'}, 'REF789'),
            ({}, None),
        ]

        for data, expected in test_cases:
            result = self.client._extract_reference(data)
            self.assertEqual(result, expected)

    def test_rejected_response(self):
        """Test rejected response generation."""
        result = self.client._rejected_response('12345678', 'Invalid ID')
        self.assertEqual(result['outcome'], 'rejected_by_iprs')
        self.assertFalse(result['verified'])
        self.assertEqual(result['error'], 'Invalid ID')

    def test_unavailable_response(self):
        """Test unavailable response generation."""
        result = self.client._unavailable_response('12345678', 'Service down')
        self.assertEqual(result['outcome'], 'iprs_unavailable')
        self.assertFalse(result['verified'])
        self.assertEqual(result['error'], 'Service down')


# Note: Retry/backoff tests are covered in the IPRS-backoff task
# and should not be duplicated here as per requirements.
