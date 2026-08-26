"""Tests for data erasure/anonymization functionality."""

from django.test import TestCase, TransactionTestCase
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import DataErasureRequest
from saccomanagement.models import SystemAuditLog
from notifications.models import Notification

User = get_user_model()


class DataErasureRequestTestCase(TestCase):
    """Test data erasure request submission and validation."""

    def setUp(self):
        """Create test users."""
        self.user = User.objects.create_user(
            email='test@example.com',
            password='testpass123',
            first_name='Test',
            last_name='User',
        )
        self.staff_user = User.objects.create_user(
            email='staff@example.com',
            password='staffpass123',
            is_staff=True,
        )
        self.client = APIClient()

    def test_user_can_submit_erasure_request(self):
        """Test that authenticated user can submit an erasure request."""
        token = RefreshToken.for_user(self.user)
        access_token = token.access_token
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {access_token}')

        response = self.client.post(
            '/api/v1/accounts/me/erasure-requests/',
            {'reason': 'I want my data deleted.'},
        )

        self.assertEqual(response.status_code, 201)
        self.assertIn('id', response.data)
        self.assertEqual(response.data['status'], 'PENDING')

        # Verify request was created
        erasure_request = DataErasureRequest.objects.get(id=response.data['id'])
        self.assertEqual(erasure_request.user, self.user)
        self.assertEqual(erasure_request.reason, 'I want my data deleted.')
        self.assertEqual(erasure_request.status, 'PENDING')

    def test_duplicate_pending_request_rejected(self):
        """Test that duplicate pending requests are rejected."""
        token = RefreshToken.for_user(self.user)
        access_token = token.access_token
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {access_token}')

        # First request
        self.client.post(
            '/api/v1/accounts/me/erasure-requests/',
            {'reason': 'First request.'},
        )

        # Second request should fail
        response = self.client.post(
            '/api/v1/accounts/me/erasure-requests/',
            {'reason': 'Second request.'},
        )

        self.assertEqual(response.status_code, 400)
        # Check for error in response
        if 'detail' in response.data:
            self.assertIn('pending', response.data['detail'].lower())
        elif 'errors' in response.data:
            self.assertIn('pending', str(response.data['errors']).lower())

    def test_unauthenticated_cannot_submit_request(self):
        """Test that unauthenticated users cannot submit requests."""
        response = self.client.post(
            '/api/v1/accounts/me/erasure-requests/',
            {'reason': 'I want my data deleted.'},
        )

        self.assertEqual(response.status_code, 401)

    def test_audit_log_created_on_request_submission(self):
        """Test that audit log entry is created when request is submitted."""
        token = RefreshToken.for_user(self.user)
        access_token = token.access_token
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {access_token}')

        response = self.client.post(
            '/api/v1/accounts/me/erasure-requests/',
            {'reason': 'Test reason.'},
        )

        self.assertEqual(response.status_code, 201)

        # Verify audit log
        audit_log = SystemAuditLog.objects.filter(
            user=self.user,
            action='CREATE',
            resource_type='DataErasureRequest',
        ).first()
        self.assertIsNotNone(audit_log)

    def test_notification_sent_on_request_submission(self):
        """Test that notification is sent when request is submitted."""
        token = RefreshToken.for_user(self.user)
        access_token = token.access_token
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {access_token}')

        response = self.client.post(
            '/api/v1/accounts/me/erasure-requests/',
            {'reason': 'Test reason.'},
        )

        self.assertEqual(response.status_code, 201)

        # Verify notification
        notification = Notification.objects.filter(
            user=self.user,
            title='Data Erasure Request Submitted',
        ).first()
        self.assertIsNotNone(notification)


class DataErasureReviewTestCase(TransactionTestCase):
    """Test staff review workflow for erasure requests."""

    def setUp(self):
        """Create test users and erasure request."""
        self.user = User.objects.create_user(
            email='test@example.com',
            password='testpass123',
            first_name='Test',
            last_name='User',
            phone_number='+254700000001',
        )
        self.staff_user = User.objects.create_user(
            email='staff@example.com',
            password='staffpass123',
            is_staff=True,
            first_name='Staff',
            last_name='User',
        )
        self.non_staff_user = User.objects.create_user(
            email='nonstaff@example.com',
            password='nonstaffpass123',
        )

        self.erasure_request = DataErasureRequest.objects.create(
            user=self.user,
            reason='I want my data deleted.',
        )

        self.client = APIClient()

    def test_staff_can_approve_request(self):
        """Test that staff can approve an erasure request."""
        token = RefreshToken.for_user(self.staff_user)
        access_token = token.access_token
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {access_token}')

        response = self.client.post(
            f'/api/v1/accounts/erasure-requests/{self.erasure_request.id}/review/',
            {'action': 'approve', 'reviewer_notes': 'Approved per user request.'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn('approved', response.data['message'].lower())

        # Verify request status
        self.erasure_request.refresh_from_db()
        self.assertEqual(self.erasure_request.status, 'COMPLETED')
        self.assertEqual(self.erasure_request.reviewed_by, self.staff_user)

    def test_staff_can_reject_request(self):
        """Test that staff can reject an erasure request."""
        token = RefreshToken.for_user(self.staff_user)
        access_token = token.access_token
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {access_token}')

        response = self.client.post(
            f'/api/v1/accounts/erasure-requests/{self.erasure_request.id}/review/',
            {'action': 'reject', 'reviewer_notes': 'Cannot delete due to active loans.'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn('rejected', response.data['message'].lower())

        # Verify request status
        self.erasure_request.refresh_from_db()
        self.assertEqual(self.erasure_request.status, 'REJECTED')
        self.assertEqual(self.erasure_request.reviewed_by, self.staff_user)

    def test_non_staff_cannot_approve_request(self):
        """Test that non-staff users cannot approve requests."""
        token = RefreshToken.for_user(self.non_staff_user)
        access_token = token.access_token
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {access_token}')

        response = self.client.post(
            f'/api/v1/accounts/erasure-requests/{self.erasure_request.id}/review/',
            {'action': 'approve'},
        )

        self.assertEqual(response.status_code, 403)

    def test_approval_anonymizes_user_data(self):
        """Test that approval anonymizes user PII."""
        token = RefreshToken.for_user(self.staff_user)
        access_token = token.access_token
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {access_token}')

        original_email = self.user.email
        original_first_name = self.user.first_name
        original_last_name = self.user.last_name
        original_phone = self.user.phone_number

        self.client.post(
            f'/api/v1/accounts/erasure-requests/{self.erasure_request.id}/review/',
            {'action': 'approve'},
        )

        # Verify user was anonymized
        self.user.refresh_from_db()
        self.assertEqual(self.user.first_name, 'Anonymized')
        self.assertEqual(self.user.last_name, 'User')
        self.assertNotEqual(self.user.email, original_email)
        self.assertIsNone(self.user.phone_number)
        self.assertFalse(self.user.is_active)

    def test_rejection_leaves_user_untouched(self):
        """Test that rejection does not modify user data."""
        token = RefreshToken.for_user(self.staff_user)
        access_token = token.access_token
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {access_token}')

        original_email = self.user.email
        original_first_name = self.user.first_name
        original_last_name = self.user.last_name
        original_phone = self.user.phone_number
        original_is_active = self.user.is_active

        self.client.post(
            f'/api/v1/accounts/erasure-requests/{self.erasure_request.id}/review/',
            {'action': 'reject', 'reviewer_notes': 'Cannot delete.'},
        )

        # Verify user was NOT modified
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, original_email)
        self.assertEqual(self.user.first_name, original_first_name)
        self.assertEqual(self.user.last_name, original_last_name)
        self.assertEqual(self.user.phone_number, original_phone)
        self.assertEqual(self.user.is_active, original_is_active)

    def test_audit_log_created_on_approval(self):
        """Test that audit log entries are created for approval."""
        token = RefreshToken.for_user(self.staff_user)
        access_token = token.access_token
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {access_token}')

        self.client.post(
            f'/api/v1/accounts/erasure-requests/{self.erasure_request.id}/review/',
            {'action': 'approve'},
        )

        # Verify audit logs for APPROVE and COMPLETE actions
        approve_log = SystemAuditLog.objects.filter(
            user=self.staff_user,
            action='APPROVE',
            resource_type='DataErasureRequest',
        ).first()
        self.assertIsNotNone(approve_log)

        complete_log = SystemAuditLog.objects.filter(
            user=self.staff_user,
            action='COMPLETE',
            resource_type='DataErasureRequest',
        ).first()
        self.assertIsNotNone(complete_log)

    def test_audit_log_created_on_rejection(self):
        """Test that audit log entry is created for rejection."""
        token = RefreshToken.for_user(self.staff_user)
        access_token = token.access_token
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {access_token}')

        self.client.post(
            f'/api/v1/accounts/erasure-requests/{self.erasure_request.id}/review/',
            {'action': 'reject', 'reviewer_notes': 'Test rejection.'},
        )

        # Verify audit log
        reject_log = SystemAuditLog.objects.filter(
            user=self.staff_user,
            action='REJECT',
            resource_type='DataErasureRequest',
        ).first()
        self.assertIsNotNone(reject_log)

    def test_notification_sent_on_approval(self):
        """Test that notification is sent on approval."""
        token = RefreshToken.for_user(self.staff_user)
        access_token = token.access_token
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {access_token}')

        self.client.post(
            f'/api/v1/accounts/erasure-requests/{self.erasure_request.id}/review/',
            {'action': 'approve'},
        )

        # Verify notification
        notification = Notification.objects.filter(
            user=self.user,
            title='Data Erasure Completed',
        ).first()
        self.assertIsNotNone(notification)

    def test_notification_sent_on_rejection(self):
        """Test that notification is sent on rejection."""
        token = RefreshToken.for_user(self.staff_user)
        access_token = token.access_token
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {access_token}')

        self.client.post(
            f'/api/v1/accounts/erasure-requests/{self.erasure_request.id}/review/',
            {'action': 'reject', 'reviewer_notes': 'Test rejection.'},
        )

        # Verify notification
        notification = Notification.objects.filter(
            user=self.user,
            title='Data Erasure Request Rejected',
        ).first()
        self.assertIsNotNone(notification)

    def test_cannot_review_already_reviewed_request(self):
        """Test that already-reviewed requests cannot be reviewed again."""
        token = RefreshToken.for_user(self.staff_user)
        access_token = token.access_token
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {access_token}')

        # First review
        self.client.post(
            f'/api/v1/accounts/erasure-requests/{self.erasure_request.id}/review/',
            {'action': 'reject', 'reviewer_notes': 'First rejection.'},
        )

        # Try to review again
        response = self.client.post(
            f'/api/v1/accounts/erasure-requests/{self.erasure_request.id}/review/',
            {'action': 'approve'},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('already been reviewed', response.data['error'].lower())

    def test_anonymization_is_idempotent(self):
        """Test that anonymization can be safely re-run."""
        token = RefreshToken.for_user(self.staff_user)
        access_token = token.access_token
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {access_token}')

        # First approval
        self.client.post(
            f'/api/v1/accounts/erasure-requests/{self.erasure_request.id}/review/',
            {'action': 'approve'},
        )

        # Verify user is anonymized
        self.user.refresh_from_db()
        first_email = self.user.email
        first_first_name = self.user.first_name

        # Create a new erasure request for the same user
        erasure_request2 = DataErasureRequest.objects.create(
            user=self.user,
            reason='Second request.',
        )

        # Approve again
        self.client.post(
            f'/api/v1/accounts/erasure-requests/{erasure_request2.id}/review/',
            {'action': 'approve'},
        )

        # Verify user is still in anonymized state (no errors)
        self.user.refresh_from_db()
        self.assertEqual(self.user.first_name, 'Anonymized')
        self.assertEqual(self.user.last_name, 'User')
        self.assertFalse(self.user.is_active)
