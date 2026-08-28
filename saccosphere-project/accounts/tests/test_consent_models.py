"""Model-level tests for UserConsent and DataConsentLog."""

from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.test import TestCase

from accounts.models import User, UserConsent
from saccomanagement.models import DataConsentLog
from saccomanagement.odpc_logging import create_data_consent_log


class UserConsentModelTestCase(TestCase):
    """Test UserConsent model constraints and validation."""

    def setUp(self):
        """Create test user."""
        self.user = User.objects.create_user(
            email='consent-test@example.com',
            phone_number='+254700000001',
            password='testpass123',
        )

    def test_unique_constraint_prevents_duplicates(self):
        """Creating duplicate (user, consent_type, version) raises IntegrityError."""
        UserConsent.objects.create(
            user=self.user,
            consent_type=UserConsent.ConsentType.TERMS,
            version='v1.0',
            consented=True,
        )

        with self.assertRaises(IntegrityError) as cm:
            UserConsent.objects.create(
                user=self.user,
                consent_type=UserConsent.ConsentType.TERMS,
                version='v1.0',
                consented=False,
            )

        # The error message format varies by database backend
        self.assertIn('unique constraint failed', str(cm.exception).lower())

    def test_different_versions_allow_same_consent_type(self):
        """Different versions of same consent type are allowed for same user."""
        UserConsent.objects.create(
            user=self.user,
            consent_type=UserConsent.ConsentType.PRIVACY,
            version='v1.0',
            consented=True,
        )

        # Should not raise - different version
        UserConsent.objects.create(
            user=self.user,
            consent_type=UserConsent.ConsentType.PRIVACY,
            version='v1.1',
            consented=True,
        )

        self.assertEqual(
            UserConsent.objects.filter(
                user=self.user,
                consent_type=UserConsent.ConsentType.PRIVACY,
            ).count(),
            2,
        )

    def test_malformed_ip_address_raises_validation_error(self):
        """Passing a malformed string to ip_address raises ValidationError."""
        with self.assertRaises(ValidationError) as cm:
            consent = UserConsent(
                user=self.user,
                consent_type=UserConsent.ConsentType.TERMS,
                version='v1.0',
                consented=True,
                ip_address='not-a-valid-ip',
            )
            consent.full_clean()

        self.assertIn('ip_address', str(cm.exception).lower())

    def test_valid_ip_address_accepted(self):
        """Valid IPv4 and IPv6 addresses are accepted."""
        # IPv4
        consent_v4 = UserConsent.objects.create(
            user=self.user,
            consent_type=UserConsent.ConsentType.MARKETING,
            version='v1.0',
            consented=True,
            ip_address='192.168.1.1',
        )
        self.assertEqual(consent_v4.ip_address, '192.168.1.1')

        # IPv6
        consent_v6 = UserConsent.objects.create(
            user=self.user,
            consent_type=UserConsent.ConsentType.DATA_PROCESSING,
            version='v1.0',
            consented=True,
            ip_address='2001:0db8:85a3:0000:0000:8a2e:0370:7334',
        )
        self.assertEqual(
            consent_v6.ip_address,
            '2001:0db8:85a3:0000:0000:8a2e:0370:7334',
        )

    def test_null_ip_address_allowed(self):
        """Null IP address is allowed for admin/migrated records."""
        consent = UserConsent.objects.create(
            user=self.user,
            consent_type=UserConsent.ConsentType.TERMS,
            version='v1.0',
            consented=True,
            ip_address=None,
        )
        self.assertIsNone(consent.ip_address)


class DataConsentLogModelTestCase(TestCase):
    """Test DataConsentLog model audit trail preservation."""

    def setUp(self):
        """Create test users."""
        self.user = User.objects.create_user(
            email='data-user@example.com',
            phone_number='+254700000002',
            password='testpass123',
        )
        self.admin = User.objects.create_user(
            email='data-admin@example.com',
            phone_number='+254700000003',
            password='testpass123',
            is_staff=True,
        )

    def test_snapshot_fields_populated_on_creation(self):
        """Snapshot fields are populated when creating a consent log."""
        log = create_data_consent_log(
            user=self.user,
            accessed_by=self.admin,
            data_type='MEMBER_PROFILE',
            reason='Account review',
        )

        self.assertIsNotNone(log.user_reference)
        self.assertIn(str(self.user.id), log.user_reference)
        self.assertIn(self.user.email, log.user_reference)

        self.assertIsNotNone(log.accessed_by_reference)
        self.assertIn(str(self.admin.id), log.accessed_by_reference)
        self.assertIn(self.admin.email, log.accessed_by_reference)

    def test_user_deletion_preserves_log_with_snapshot(self):
        """Deleting a user preserves the DataConsentLog with snapshot intact."""
        # Create a consent log
        log = create_data_consent_log(
            user=self.user,
            accessed_by=self.admin,
            data_type='LOAN_DETAILS',
            reason='Loan application review',
        )

        # Store snapshot values before deletion
        user_ref_before = log.user_reference
        accessed_by_ref_before = log.accessed_by_reference

        # Delete the user
        self.user.delete()

        # Refresh log from database
        log.refresh_from_db()

        # FK should be null but snapshot preserved
        self.assertIsNone(log.user)
        self.assertEqual(log.user_reference, user_ref_before)

        # accessed_by should still be intact
        self.assertEqual(log.accessed_by, self.admin)
        self.assertEqual(log.accessed_by_reference, accessed_by_ref_before)

    def test_accessed_by_deletion_preserves_log_with_snapshot(self):
        """Deleting the accessed_by user preserves the DataConsentLog with snapshot."""
        # Create a consent log
        log = create_data_consent_log(
            user=self.user,
            accessed_by=self.admin,
            data_type='MEMBER_STATEMENT',
            reason='Statement download',
        )

        # Store snapshot values before deletion
        user_ref_before = log.user_reference
        accessed_by_ref_before = log.accessed_by_reference

        # Delete the admin
        self.admin.delete()

        # Refresh log from database
        log.refresh_from_db()

        # FK should be null but snapshot preserved
        self.assertIsNone(log.accessed_by)
        self.assertEqual(log.accessed_by_reference, accessed_by_ref_before)

        # user should still be intact
        self.assertEqual(log.user, self.user)
        self.assertEqual(log.user_reference, user_ref_before)

    def test_both_users_deletion_preserves_both_snapshots(self):
        """Deleting both users preserves both snapshot fields."""
        # Create a consent log
        log = create_data_consent_log(
            user=self.user,
            accessed_by=self.admin,
            data_type='KYC_DOCUMENTS',
            reason='KYC verification',
        )

        # Store snapshot values
        user_ref_before = log.user_reference
        accessed_by_ref_before = log.accessed_by_reference

        # Delete both users
        self.user.delete()
        self.admin.delete()

        # Refresh log from database
        log.refresh_from_db()

        # Both FKs should be null but snapshots preserved
        self.assertIsNone(log.user)
        self.assertIsNone(log.accessed_by)
        self.assertEqual(log.user_reference, user_ref_before)
        self.assertEqual(log.accessed_by_reference, accessed_by_ref_before)
