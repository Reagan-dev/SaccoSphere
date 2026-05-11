"""Test account maintenance Celery tasks."""

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from accounts.models import OTPToken, User
from accounts.tasks import cleanup_expired_otps


class AccountTaskTestCase(TestCase):
    """Test account maintenance task behavior."""

    def setUp(self):
        """Create a user for OTP task tests."""
        self.user = User.objects.create_user(
            email='task-user@example.com',
            first_name='Task',
            last_name='User',
            phone_number='254700000001',
            password='testpass123',
        )

    def create_token(self, code, expires_at, is_used):
        """Create an OTP token for cleanup tests."""
        return OTPToken.objects.create(
            user=self.user,
            phone_number=self.user.phone_number,
            code=code,
            purpose=OTPToken.Purpose.PHONE_VERIFY,
            expires_at=expires_at,
            is_used=is_used,
        )

    def test_cleanup_removes_expired_tokens(self):
        """Test cleanup deletes expired used and abandoned unused tokens."""
        now = timezone.now()
        expired_used = self.create_token(
            code='111111',
            expires_at=now - timedelta(minutes=10),
            is_used=True,
        )
        abandoned_unused = self.create_token(
            code='222222',
            expires_at=now + timedelta(minutes=10),
            is_used=False,
        )
        OTPToken.objects.filter(id=abandoned_unused.id).update(
            created_at=now - timedelta(hours=25),
        )
        active_unused = self.create_token(
            code='333333',
            expires_at=now + timedelta(minutes=10),
            is_used=False,
        )

        deleted_count = cleanup_expired_otps()

        self.assertEqual(deleted_count, 2)
        self.assertFalse(OTPToken.objects.filter(id=expired_used.id).exists())
        self.assertFalse(
            OTPToken.objects.filter(id=abandoned_unused.id).exists()
        )
        self.assertTrue(OTPToken.objects.filter(id=active_unused.id).exists())
