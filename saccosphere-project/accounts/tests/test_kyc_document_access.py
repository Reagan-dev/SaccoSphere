"""Tests for KYC document access logging and signed URL generation."""

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model
from django.core.signing import TimestampSigner
from rest_framework import status
from rest_framework.test import APITestCase
from saccomanagement.models import SystemAuditLog
from unittest.mock import MagicMock, patch

from accounts.kyc_document_access import (
    _generate_local_signed_url,
    generate_kyc_document_url,
)
from accounts.models import KYCVerification

User = get_user_model()


class KYCDocumentAccessTestCase(TestCase):
    """Test KYC document access logging and signed URL generation."""

    def setUp(self):
        """Set up test user and KYC verification."""
        self.user = User.objects.create_user(
            phone_number='+254700000001',
            email='test@example.com',
        )
        self.user2 = User.objects.create_user(
            phone_number='+254700000003',
            email='test2@example.com',
        )
        self.viewer = User.objects.create_user(
            phone_number='+254700000002',
            email='viewer@example.com',
            is_staff=True,
        )
        self.kyc = KYCVerification.objects.create(
            user=self.user,
            status=KYCVerification.Status.PENDING,
            id_number='12345678',
        )
        # Create a mock document file
        from django.core.files.uploadedfile import SimpleUploadedFile
        self.kyc.id_front = SimpleUploadedFile(
            'test_front.jpg',
            b'fake image data',
            content_type='image/jpeg',
        )
        self.kyc.save()

    @override_settings(STORAGE_BACKEND='local')
    def test_generate_url_creates_audit_log(self):
        """
        Test that generating a signed URL creates exactly one audit entry
        with the correct viewer identity.
        """
        # Clear any existing audit logs
        SystemAuditLog.objects.all().delete()

        # Create a mock request
        request = MagicMock()
        request.META = {
            'REMOTE_ADDR': '192.168.1.1',
            'HTTP_USER_AGENT': 'Test Browser',
        }

        # Generate a signed URL for id_front (without mocking)
        url = generate_kyc_document_url(
            kyc_verification=self.kyc,
            document_field='id_front',
            viewer=self.viewer,
            request=request,
        )

        # Verify URL was generated
        self.assertIsNotNone(url)
        self.assertIn(str(self.kyc.id), url)

        # Verify exactly one audit log was created
        audit_logs = SystemAuditLog.objects.filter(
            action='KYC_DOCUMENT_ACCESS',
            resource_type='KYCDocument',
            resource_id=str(self.kyc.id),
        )
        self.assertEqual(audit_logs.count(), 1)

        # Verify audit log has correct viewer identity
        audit_log = audit_logs.first()
        self.assertEqual(audit_log.user, self.viewer)
        self.assertEqual(audit_log.ip_address, '192.168.1.1')
        self.assertEqual(audit_log.user_agent, 'Test Browser')

        # Verify audit log has correct document details
        self.assertEqual(
            audit_log.new_values['document_field'],
            'id_front',
        )
        self.assertEqual(
            audit_log.new_values['viewer_email'],
            'viewer@example.com',
        )

    @override_settings(STORAGE_BACKEND='local')
    def test_generate_url_for_nonexistent_document_returns_none(self):
        """
        Test that generating a URL for a non-existent document returns None.
        """
        # Create a KYC verification without any documents
        kyc_no_doc = KYCVerification.objects.create(
            user=self.user2,
            status=KYCVerification.Status.PENDING,
            id_number='87654321',
        )

        # Clear any existing audit logs
        SystemAuditLog.objects.all().delete()

        # Try to generate URL for a document that doesn't exist
        url = generate_kyc_document_url(
            kyc_verification=kyc_no_doc,
            document_field='id_front',  # No document uploaded
            viewer=self.viewer,
        )

        # Verify None is returned
        self.assertIsNone(url)

        # Verify no audit log was created
        audit_logs = SystemAuditLog.objects.filter(
            action='KYC_DOCUMENT_ACCESS',
        )
        self.assertEqual(audit_logs.count(), 0)

    @override_settings(STORAGE_BACKEND='local')
    def test_generate_url_invalid_document_field_raises_error(self):
        """
        Test that an invalid document field raises ValueError.
        """
        with self.assertRaises(ValueError) as context:
            generate_kyc_document_url(
                kyc_verification=self.kyc,
                document_field='invalid_field',
                viewer=self.viewer,
            )

        self.assertIn('Invalid document_field', str(context.exception))

    @override_settings(STORAGE_BACKEND='local')
    def test_generate_local_signed_url(self):
        """
        Test that local signed URL generation creates a valid token.
        """
        from accounts.kyc_document_access import _generate_local_signed_url

        url = _generate_local_signed_url(
            self.kyc,
            'id_front',
            expiration_minutes=15,
        )

        # Verify URL contains the expected components
        self.assertIn(str(self.kyc.id), url)
        self.assertIn('id_front', url)

        # Verify token can be decoded (token is the last segment before trailing slash)
        signer = TimestampSigner()
        # URL format: /api/v1/accounts/kyc/documents/{kyc_id}/{document_field}/{token}/
        parts = url.rstrip('/').split('/')
        token = parts[-1]
        data = signer.unsign_object(token, max_age=15 * 60)
        self.assertEqual(data['kyc_id'], str(self.kyc.id))
        self.assertEqual(data['document_field'], 'id_front')

    @override_settings(STORAGE_BACKEND='s3')
    @patch('accounts.kyc_document_access._generate_s3_presigned_url')
    def test_generate_s3_presigned_url(self, mock_s3_url):
        """
        Test that S3 presigned URL generation is called correctly.
        """
        mock_s3_url.return_value = 'https://s3.amazonaws.com/bucket/key?signature=...'

        # Create a mock request
        request = MagicMock()
        request.META = {'REMOTE_ADDR': '192.168.1.1'}

        url = generate_kyc_document_url(
            kyc_verification=self.kyc,
            document_field='id_front',
            viewer=self.viewer,
            request=request,
        )

        # Verify S3 URL generation was called
        mock_s3_url.assert_called_once()
        self.assertEqual(url, 'https://s3.amazonaws.com/bucket/key?signature=...')

    def test_multiple_accesses_create_multiple_audit_logs(self):
        """
        Test that multiple URL generations create separate audit logs.
        """
        SystemAuditLog.objects.all().delete()

        request = MagicMock()
        request.META = {'REMOTE_ADDR': '192.168.1.1'}

        # Generate URL twice (without mocking)
        generate_kyc_document_url(
            kyc_verification=self.kyc,
            document_field='id_front',
            viewer=self.viewer,
            request=request,
        )
        generate_kyc_document_url(
            kyc_verification=self.kyc,
            document_field='id_front',
            viewer=self.viewer,
            request=request,
        )

        # Verify two audit logs were created
        audit_logs = SystemAuditLog.objects.filter(
            action='KYC_DOCUMENT_ACCESS',
        )
        self.assertEqual(audit_logs.count(), 2)

        # Verify both have the same viewer
        for log in audit_logs:
            self.assertEqual(log.user, self.viewer)


@override_settings(STORAGE_BACKEND='local')
class KYCDocumentServeViewTestCase(APITestCase):
    """
    Test audit logging in KYCDocumentServeView, the API-facing endpoint that
    actually serves KYC document bytes (as opposed to generate_kyc_document_url,
    which only issues the signed URL for the admin console).
    """

    def setUp(self):
        self.owner = User.objects.create_user(
            phone_number='+254700000010',
            email='owner@example.com',
        )
        self.staff = User.objects.create_user(
            phone_number='+254700000011',
            email='staffviewer@example.com',
            is_staff=True,
        )
        self.other_member = User.objects.create_user(
            phone_number='+254700000012',
            email='other@example.com',
        )
        self.kyc = KYCVerification.objects.create(
            user=self.owner,
            status=KYCVerification.Status.PENDING,
            id_number='55555555',
        )
        self.kyc.id_front = SimpleUploadedFile(
            'front.jpg',
            b'fake image data',
            content_type='image/jpeg',
        )
        self.kyc.save()
        self.url = _generate_local_signed_url(
            self.kyc, 'id_front', expiration_minutes=15,
        )

    def test_staff_viewing_another_members_document_logs_staff_access(self):
        SystemAuditLog.objects.all().delete()
        self.client.force_authenticate(user=self.staff)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        log = SystemAuditLog.objects.get(
            action='KYC_DOCUMENT_ACCESS',
            resource_type='KYCDocument',
            resource_id=str(self.kyc.id),
        )
        self.assertEqual(log.user, self.staff)
        self.assertEqual(log.new_values['access_type'], 'staff')
        self.assertEqual(log.new_values['document_field'], 'id_front')
        self.assertEqual(
            log.new_values['document_owner_email'],
            'owner@example.com',
        )

    def test_member_viewing_own_document_logs_self_access(self):
        SystemAuditLog.objects.all().delete()
        self.client.force_authenticate(user=self.owner)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        log = SystemAuditLog.objects.get(
            action='KYC_DOCUMENT_ACCESS',
            resource_type='KYCDocument',
            resource_id=str(self.kyc.id),
        )
        self.assertEqual(log.user, self.owner)
        self.assertEqual(log.new_values['access_type'], 'self')

    def test_unauthorized_viewer_is_denied_and_logged(self):
        SystemAuditLog.objects.all().delete()
        self.client.force_authenticate(user=self.other_member)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        log = SystemAuditLog.objects.get(
            action='KYC_DOCUMENT_ACCESS_DENIED',
            resource_type='KYCDocument',
            resource_id=str(self.kyc.id),
        )
        self.assertEqual(log.user, self.other_member)
        self.assertEqual(
            log.new_values['document_owner_email'],
            'owner@example.com',
        )
        # A denied attempt must not also produce a successful-access record.
        self.assertFalse(
            SystemAuditLog.objects.filter(
                action='KYC_DOCUMENT_ACCESS',
            ).exists(),
        )
