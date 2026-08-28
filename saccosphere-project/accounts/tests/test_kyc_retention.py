"""Tests for KYC document retention and erasure functionality."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import KYCVerification, DataErasureRequest
from saccomanagement.models import SystemAuditLog

User = get_user_model()


class KYCRetentionCleanupTestCase(TestCase):
    """Test scheduled KYC retention cleanup job."""

    def setUp(self):
        """Set up test users and KYC records."""
        self.user = User.objects.create_user(
            phone_number='+254700000001',
            email='test@example.com',
        )
        self.user2 = User.objects.create_user(
            phone_number='+254700000002',
            email='test2@example.com',
        )

    @override_settings(KYC_RETENTION_DAYS=7)
    def test_expired_record_is_cleaned_up(self):
        """
        Test that a KYC record past retention_until is cleaned up.
        """
        # Create a KYC record past retention
        past_date = timezone.now() - timedelta(days=10)
        kyc = KYCVerification.objects.create(
            user=self.user,
            status=KYCVerification.Status.APPROVED,
            id_number='12345678',
            submitted_at=past_date,
            retention_until=past_date + timedelta(days=7),
        )
        kyc.id_number = '12345678'
        kyc.save()

        # Clear audit logs
        SystemAuditLog.objects.all().delete()

        # Run cleanup command
        call_command('cleanup_expired_kyc')

        # Refresh and verify cleanup
        kyc.refresh_from_db()
        self.assertIsNone(kyc.id_number)
        self.assertIsNone(kyc.normalized_id_number)
        self.assertEqual(kyc.status, KYCVerification.Status.REJECTED)
        self.assertIn('retention policy', kyc.rejection_reason)

        # Verify audit log was created
        audit_logs = SystemAuditLog.objects.filter(
            action='KYC_RETENTION_CLEANUP',
            resource_id=str(kyc.id),
        )
        self.assertEqual(audit_logs.count(), 1)

    @override_settings(KYC_RETENTION_DAYS=7)
    def test_not_yet_expired_record_is_left_alone(self):
        """
        Test that a KYC record not yet past retention_until is not cleaned up.
        """
        # Create a KYC record within retention period
        recent_date = timezone.now() - timedelta(days=5)
        kyc = KYCVerification.objects.create(
            user=self.user,
            status=KYCVerification.Status.APPROVED,
            id_number='12345678',
            submitted_at=recent_date,
            retention_until=recent_date + timedelta(days=7),
        )
        kyc.id_number = '12345678'
        kyc.save()

        original_id_number = kyc.id_number

        # Run cleanup command
        call_command('cleanup_expired_kyc')

        # Refresh and verify no cleanup
        kyc.refresh_from_db()
        self.assertEqual(kyc.id_number, original_id_number)
        self.assertNotEqual(kyc.status, KYCVerification.Status.REJECTED)

    @override_settings(KYC_RETENTION_DAYS=None)
    def test_cleanup_disabled_when_retention_not_configured(self):
        """
        Test that cleanup is skipped when KYC_RETENTION_DAYS is not configured.
        """
        # Create a KYC record past retention
        past_date = timezone.now() - timedelta(days=10)
        kyc = KYCVerification.objects.create(
            user=self.user,
            status=KYCVerification.Status.APPROVED,
            id_number='12345678',
            submitted_at=past_date,
            retention_until=past_date + timedelta(days=7),
        )
        kyc.id_number = '12345678'
        kyc.save()

        # Run cleanup command
        call_command('cleanup_expired_kyc')

        # Refresh and verify no cleanup
        kyc.refresh_from_db()
        self.assertEqual(kyc.id_number, '12345678')

    @override_settings(KYC_RETENTION_DAYS=7)
    def test_dry_run_does_not_delete(self):
        """
        Test that dry-run mode shows what would be deleted without deleting.
        """
        # Create a KYC record past retention
        past_date = timezone.now() - timedelta(days=10)
        kyc = KYCVerification.objects.create(
            user=self.user,
            status=KYCVerification.Status.APPROVED,
            id_number='12345678',
            submitted_at=past_date,
            retention_until=past_date + timedelta(days=7),
        )
        kyc.id_number = '12345678'
        kyc.save()

        # Run cleanup command with dry-run
        call_command('cleanup_expired_kyc', '--dry-run')

        # Refresh and verify no cleanup
        kyc.refresh_from_db()
        self.assertEqual(kyc.id_number, '12345678')


class ErasureRequestEndpointTestCase(TestCase):
    """Test user-initiated erasure request endpoint."""

    def setUp(self):
        """Set up test user and client."""
        self.user = User.objects.create_user(
            phone_number='+254700000001',
            email='test@example.com',
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_erasure_request_executes_immediately_without_hold(self):
        """
        Test that erasure request executes immediately when no hold applies.
        """
        # Create a KYC record
        kyc = KYCVerification.objects.create(
            user=self.user,
            status=KYCVerification.Status.APPROVED,
            id_number='12345678',
        )
        kyc.id_number = '12345678'
        kyc.save()

        # Clear audit logs
        SystemAuditLog.objects.all().delete()

        # Submit erasure request
        response = self.client.post(
            '/api/v1/accounts/me/erasure-requests/',
            {'reason': 'I want my data deleted'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['status'], 'COMPLETED')

        # Verify KYC was anonymized
        kyc.refresh_from_db()
        self.assertIsNone(kyc.id_number)

        # Verify audit log was created
        audit_logs = SystemAuditLog.objects.filter(
            action='KYC_ERASURE',
            resource_id=str(kyc.id),
        )
        self.assertEqual(audit_logs.count(), 1)

    def test_erasure_request_queues_when_hold_applies(self):
        """
        Test that erasure request is queued when a hold applies.
        """
        # Create a KYC record
        kyc = KYCVerification.objects.create(
            user=self.user,
            status=KYCVerification.Status.APPROVED,
            id_number='12345678',
        )
        kyc.id_number = '12345678'
        kyc.save()

        # Create an active hold (existing erasure request on hold)
        hold = DataErasureRequest.objects.create(
            user=self.user,
            status=DataErasureRequest.Status.ON_HOLD,
            hold_reason=DataErasureRequest.HoldReason.REGULATORY_INVESTIGATION,
            hold_until=timezone.now() + timedelta(days=30),
            reason='Previous erasure request on hold',
        )

        # Submit erasure request
        response = self.client.post(
            '/api/v1/accounts/me/erasure-requests/',
            {'reason': 'I want my data deleted'},
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.data['status'], 'ON_HOLD')
        self.assertIn('hold', response.data['message'].lower())

        # Verify KYC was NOT anonymized
        kyc.refresh_from_db()
        self.assertEqual(kyc.id_number, '12345678')

        # Verify the new request is on hold
        new_request = DataErasureRequest.objects.filter(
            user=self.user,
        ).order_by('-requested_at').first()
        self.assertEqual(new_request.status, DataErasureRequest.Status.ON_HOLD)

    def test_queued_erasure_executes_when_hold_cleared(self):
        """
        Test that a queued erasure request executes when hold is cleared.
        """
        from accounts.kyc_retention import process_queued_erasure_requests

        # Create a KYC record
        kyc = KYCVerification.objects.create(
            user=self.user,
            status=KYCVerification.Status.APPROVED,
            id_number='12345678',
        )
        kyc.id_number = '12345678'
        kyc.save()

        # Create a queued erasure request with expired hold
        past_date = timezone.now() - timedelta(days=1)
        erasure_request = DataErasureRequest.objects.create(
            user=self.user,
            status=DataErasureRequest.Status.ON_HOLD,
            hold_reason=DataErasureRequest.HoldReason.REGULATORY_INVESTIGATION,
            hold_until=past_date,
        )

        # Process queued requests
        process_queued_erasure_requests()

        # Verify KYC was anonymized
        kyc.refresh_from_db()
        self.assertIsNone(kyc.id_number)

        # Verify request was marked as completed
        erasure_request.refresh_from_db()
        self.assertEqual(erasure_request.status, DataErasureRequest.Status.COMPLETED)


class KYCDeletionCascadeTestCase(TestCase):
    """Test that deleting KYC records doesn't break foreign-key relationships."""

    def setUp(self):
        """Set up test user and KYC record."""
        self.user = User.objects.create_user(
            phone_number='+254700000001',
            email='test@example.com',
        )
        self.kyc = KYCVerification.objects.create(
            user=self.user,
            status=KYCVerification.Status.APPROVED,
            id_number='12345678',
        )
        self.kyc.id_number = '12345678'
        self.kyc.save()

    def test_anonymizing_kyc_does_not_orphan_user(self):
        """
        Test that anonymizing a KYC record doesn't delete or orphan the user.
        """
        from accounts.kyc_retention import anonymize_kyc_record

        user_id = self.user.id

        # Anonymize KYC
        anonymize_kyc_record(self.kyc, triggered_by=self.user, reason='Test')

        # Verify user still exists
        self.assertTrue(User.objects.filter(id=user_id).exists())

        # Verify KYC still exists but is anonymized
        self.kyc.refresh_from_db()
        self.assertIsNone(self.kyc.id_number)
        self.assertEqual(self.kyc.user.id, user_id)

    def test_deleting_user_cascades_to_kyc(self):
        """
        Test that deleting a user cascades to KYC (OneToOneField).
        """
        kyc_id = self.kyc.id

        # Delete user
        self.user.delete()

        # Verify KYC is also deleted (CASCADE)
        self.assertFalse(KYCVerification.objects.filter(id=kyc_id).exists())

    def test_s3_object_deletion_on_anonymization(self):
        """
        Test that S3 objects are deleted when KYC is anonymized.
        """
        from accounts.kyc_retention import anonymize_kyc_record
        from django.core.files.uploadedfile import SimpleUploadedFile

        # Add a mock document
        self.kyc.id_front = SimpleUploadedFile(
            'test_front.jpg',
            b'fake image data',
            content_type='image/jpeg',
        )
        self.kyc.save()

        # Anonymize KYC
        anonymize_kyc_record(self.kyc, triggered_by=self.user, reason='Test')

        # Verify document field is cleared
        self.kyc.refresh_from_db()
        self.assertFalse(self.kyc.id_front)
