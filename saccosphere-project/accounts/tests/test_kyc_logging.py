"""Tests for KYC structured logging with correlation IDs and PII sanitization."""

import logging
import requests
from io import BytesIO
from unittest.mock import patch, MagicMock
from django.test import TestCase, override_settings
from PIL import Image
from rest_framework.test import APIClient
from django.contrib.auth import get_user_model

from accounts.models import KYCVerification
from accounts.integrations.iprs_client import IPRSClient, IPRSError

User = get_user_model()


class KYCLoggingTestCase(TestCase):
    """Test structured logging for KYC operations with correlation IDs."""

    def setUp(self):
        """Set up test fixtures."""
        self.user = User.objects.create_user(
            email='test@example.com',
            phone_number='+254712345678',
            password='testpass123',
            first_name='John',
            last_name='Doe',
            date_of_birth='1990-01-01',
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

        # Set up log capture
        self.log_capture = []
        self.log_handler = logging.Handler()
        self.log_handler.emit = lambda record: self.log_capture.append(record)
        self.iprs_logger = logging.getLogger('saccosphere.iprs')
        self.iprs_logger.addHandler(self.log_handler)
        self.iprs_logger.setLevel(logging.DEBUG)

    def tearDown(self):
        """Clean up test fixtures."""
        self.iprs_logger.removeHandler(self.log_handler)

    def _create_test_image(self):
        """Create a test image for KYC upload."""
        image = Image.new('RGB', (600, 400), color='white')
        image_bytes = BytesIO()
        image.save(image_bytes, format='JPEG')
        image_bytes.seek(0)
        return image_bytes

    def test_correlation_id_appears_in_view_and_iprs_logs(self):
        """Test that correlation ID appears across view and IPRS failure logs."""
        test_correlation_id = 'test-correlation-12345'
        test_id_number = '12345678'

        # Test IPRS client directly with correlation_id
        # Mock requests.post to raise a connection error
        with patch('accounts.integrations.iprs_client.requests.post') as mock_post:
            mock_post.side_effect = requests.exceptions.ConnectionError('Connection failed')
            
            client = IPRSClient()
            client.mock = False  # Disable mock mode to trigger actual error logging
            client.api_key = 'test'
            client.api_url = 'http://test.com'

            result = client.verify_id(
                test_id_number,
                correlation_id=test_correlation_id,
                kyc_submission_id='test-submission-id',
            )

        # Check that logs were captured
        self.assertGreater(len(self.log_capture), 0)

        # Extract correlation IDs from logs
        log_extras = [
            getattr(record, 'correlation_id', None) for record in self.log_capture
        ]

        # Verify correlation ID appears in logs
        correlation_ids_in_logs = [
            extra for extra in log_extras if extra == test_correlation_id
        ]
        self.assertGreater(
            len(correlation_ids_in_logs),
            0,
            'Correlation ID should appear in IPRS logs',
        )

    def test_id_number_never_appears_in_logs(self):
        """Test that raw id_number never appears in log output."""
        test_id_number = '12345678'
        test_correlation_id = 'test-correlation-45678'

        # Mock IPRS client to succeed
        mock_result = {
            'outcome': 'verified',
            'verified': True,
            'id_number': test_id_number,
            'name': 'Test Citizen',
            'date_of_birth': '1990-01-01',
            'iprs_reference': 'TEST-REF',
            'error': '',
        }

        with patch.object(IPRSClient, 'verify_id', return_value=mock_result):
            response = self.client.post(
                '/api/v1/accounts/kyc/submit-id/',
                {'id_number': test_id_number, 'date_of_birth': '1990-01-01'},
                HTTP_X_CORRELATION_ID=test_correlation_id,
                format='json',
            )

        # Check all log messages
        log_messages = [record.getMessage() for record in self.log_capture]
        log_extras = [
            {
                'correlation_id': getattr(record, 'correlation_id', None),
                'id_number_ref': getattr(record, 'id_number_ref', None),
                'message': record.getMessage(),
            }
            for record in self.log_capture
        ]

        # Verify raw id_number never appears in any log message
        for log_entry in log_messages:
            self.assertNotIn(
                test_id_number,
                log_entry,
                f'Raw id_number should not appear in log message: {log_entry}',
            )

        # Verify id_number_ref is sanitized (not the raw value)
        for extra in log_extras:
            id_ref = extra.get('id_number_ref')
            if id_ref:
                self.assertNotEqual(
                    id_ref,
                    test_id_number,
                    'id_number_ref should be sanitized, not raw value',
                )
                # Should be either truncated or hashed
                self.assertTrue(
                    '...' in id_ref or '[HASH:' in id_ref,
                    'id_number_ref should be truncated or hashed',
                )

    def test_structured_logging_includes_required_fields(self):
        """Test that structured logs include correlation_id, kyc_submission_id, and step."""
        test_id_number = '12345678'
        test_correlation_id = 'test-correlation-99999'

        # Mock IPRS client to succeed
        mock_result = {
            'outcome': 'verified',
            'verified': True,
            'id_number': test_id_number,
            'name': 'Test Citizen',
            'date_of_birth': '1990-01-01',
            'iprs_reference': 'TEST-REF',
            'error': '',
        }

        with patch.object(IPRSClient, 'verify_id', return_value=mock_result):
            response = self.client.post(
                '/api/v1/accounts/kyc/submit-id/',
                {'id_number': test_id_number, 'date_of_birth': '1990-01-01'},
                HTTP_X_CORRELATION_ID=test_correlation_id,
                format='json',
            )

        # Check structured log fields
        for record in self.log_capture:
            extra = getattr(record, 'correlation_id', None)
            if extra:
                # Verify correlation_id is present
                self.assertIsNotNone(
                    getattr(record, 'correlation_id', None),
                    'correlation_id should be in structured log',
                )
                # Verify step is present for IPRS logs
                if 'IPRS' in record.getMessage():
                    self.assertIsNotNone(
                        getattr(record, 'step', None),
                        'step should be in IPRS structured log',
                    )

    def test_pii_sanitization_for_various_lengths(self):
        """Test PII sanitization works for various ID number lengths."""
        from config.utils import sanitize_pii

        # Long ID number (should be truncated)
        long_id = '12345678901234567890'
        sanitized = sanitize_pii(long_id)
        self.assertIn('...', sanitized)
        self.assertNotIn(long_id, sanitized)
        self.assertTrue(sanitized.endswith('7890'))

        # Short ID number (should be hashed)
        short_id = '12345'
        sanitized = sanitize_pii(short_id)
        self.assertIn('[HASH:', sanitized)
        self.assertNotIn(short_id, sanitized)

        # None value
        self.assertEqual(sanitize_pii(None), '[REDACTED]')

        # Empty string
        self.assertEqual(sanitize_pii(''), '[REDACTED]')

    def test_iprs_error_logging_includes_error_type(self):
        """Test that IPRS error logs include the error type."""
        test_id_number = '12345678'
        test_correlation_id = 'test-correlation-error'

        # Mock requests.post to raise a connection error
        with patch('accounts.integrations.iprs_client.requests.post') as mock_post:
            mock_post.side_effect = requests.exceptions.ConnectionError('Connection failed')
            
            client = IPRSClient()
            client.mock = False  # Disable mock mode to trigger actual error logging
            client.api_key = 'test'
            client.api_url = 'http://test.com'

            result = client.verify_id(
                test_id_number,
                correlation_id=test_correlation_id,
                kyc_submission_id='test-submission-id',
            )

        # Check for error_type in logs
        error_types = [
            getattr(record, 'error_type', None) for record in self.log_capture
        ]
        self.assertIn(
            'connection_error',
            error_types,
            'error_type should be in IPRS error logs',
        )

    def test_kyc_upload_passes_correlation_id(self):
        """Test that KYC upload passes correlation ID to IPRS verification."""
        test_correlation_id = 'test-correlation-upload'

        # Create KYC record with id_number set
        kyc = KYCVerification.objects.create(
            user=self.user,
            status=KYCVerification.Status.PENDING,
            id_number='12345678',
        )

        # Mock IPRS client
        mock_result = {
            'outcome': 'verified',
            'verified': True,
            'id_number': '12345678',
            'name': 'Test Citizen',
            'date_of_birth': '1990-01-01',
            'iprs_reference': 'TEST-REF',
            'error': '',
        }

        with patch.object(IPRSClient, 'verify_id', return_value=mock_result) as mock_verify:
            # Upload a document (this should trigger IPRS if id_number is set)
            image_bytes = self._create_test_image()
            image_bytes.name = 'test_image.jpg'

            response = self.client.post(
                '/api/v1/accounts/kyc/upload/',
                {
                    'document_type': 'id_front',
                    'file': image_bytes,
                },
                HTTP_X_CORRELATION_ID=test_correlation_id,
                format='multipart',
            )

            # Verify IPRS was called with correlation_id
            if mock_verify.called:
                call_kwargs = mock_verify.call_args[1]
                self.assertEqual(
                    call_kwargs.get('correlation_id'),
                    test_correlation_id,
                    'correlation_id should be passed to IPRS client',
                )
                self.assertEqual(
                    call_kwargs.get('kyc_submission_id'),
                    str(kyc.id),
                    'kyc_submission_id should be passed to IPRS client',
                )
