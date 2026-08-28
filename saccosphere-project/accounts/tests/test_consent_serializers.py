"""Serializer-level tests for consent management serializers."""

from django.test import TestCase
from rest_framework import serializers

from accounts.models import User, UserConsent
from accounts.serializers import (
    ConsentGiveSerializer,
    ConsentSerializer,
    DataConsentLogSerializer,
)
from saccomanagement.models import DataConsentLog
from saccomanagement.odpc_logging import create_data_consent_log


class ConsentGiveSerializerTestCase(TestCase):
    """Test ConsentGiveSerializer validation and behavior."""

    def setUp(self):
        """Create test user."""
        self.user = User.objects.create_user(
            email='serializer-test@example.com',
            phone_number='+254700000001',
            password='testpass123',
        )

    def test_valid_input_accepted(self):
        """Valid consent input is accepted."""
        data = {
            'consent_type': UserConsent.ConsentType.TERMS,
            'version': 'v1.0',
            'consented': True,
        }
        serializer = ConsentGiveSerializer(data=data)
        self.assertTrue(serializer.is_valid())
        self.assertEqual(serializer.validated_data['consent_type'], UserConsent.ConsentType.TERMS)
        self.assertEqual(serializer.validated_data['version'], 'v1.0')
        self.assertTrue(serializer.validated_data['consented'])

    def test_invalid_consent_type_rejected(self):
        """Invalid consent_type is rejected."""
        data = {
            'consent_type': 'INVALID_TYPE',
            'version': 'v1.0',
            'consented': True,
        }
        serializer = ConsentGiveSerializer(data=data)
        self.assertFalse(serializer.is_valid())
        self.assertIn('consent_type', serializer.errors)

    def test_empty_version_rejected(self):
        """Empty version is rejected."""
        data = {
            'consent_type': UserConsent.ConsentType.PRIVACY,
            'version': '',
            'consented': True,
        }
        serializer = ConsentGiveSerializer(data=data)
        self.assertFalse(serializer.is_valid())
        self.assertIn('version', serializer.errors)

    def test_version_without_v_prefix_rejected(self):
        """Version without 'v' prefix is rejected."""
        data = {
            'consent_type': UserConsent.ConsentType.DATA_PROCESSING,
            'version': '1.0',
            'consented': True,
        }
        serializer = ConsentGiveSerializer(data=data)
        self.assertFalse(serializer.is_valid())
        self.assertIn('version', serializer.errors)

    def test_client_supplied_user_rejected(self):
        """Client-supplied user field is rejected."""
        data = {
            'user': str(self.user.id),
            'consent_type': UserConsent.ConsentType.TERMS,
            'version': 'v1.0',
            'consented': True,
        }
        serializer = ConsentGiveSerializer(data=data)
        self.assertFalse(serializer.is_valid())
        self.assertIn('non_field_errors', serializer.errors)

    def test_client_supplied_ip_address_rejected(self):
        """Client-supplied ip_address field is rejected."""
        data = {
            'consent_type': UserConsent.ConsentType.TERMS,
            'version': 'v1.0',
            'consented': True,
            'ip_address': '192.168.1.1',
        }
        serializer = ConsentGiveSerializer(data=data)
        self.assertFalse(serializer.is_valid())
        self.assertIn('non_field_errors', serializer.errors)

    def test_client_supplied_timestamp_rejected(self):
        """Client-supplied timestamp field is rejected."""
        data = {
            'consent_type': UserConsent.ConsentType.TERMS,
            'version': 'v1.0',
            'consented': True,
            'timestamp': '2024-01-01T00:00:00Z',
        }
        serializer = ConsentGiveSerializer(data=data)
        self.assertFalse(serializer.is_valid())
        self.assertIn('non_field_errors', serializer.errors)

    def test_client_supplied_user_agent_rejected(self):
        """Client-supplied user_agent field is rejected."""
        data = {
            'consent_type': UserConsent.ConsentType.TERMS,
            'version': 'v1.0',
            'consented': True,
            'user_agent': 'Mozilla/5.0',
        }
        serializer = ConsentGiveSerializer(data=data)
        self.assertFalse(serializer.is_valid())
        self.assertIn('non_field_errors', serializer.errors)

    def test_all_consent_types_valid(self):
        """All defined consent types are valid."""
        for consent_type in UserConsent.ConsentType.values:
            data = {
                'consent_type': consent_type,
                'version': 'v1.0',
                'consented': True,
            }
            serializer = ConsentGiveSerializer(data=data)
            self.assertTrue(
                serializer.is_valid(),
                f'consent_type {consent_type} should be valid'
            )


class ConsentSerializerTestCase(TestCase):
    """Test ConsentSerializer read-only behavior."""

    def setUp(self):
        """Create test user and consent record."""
        self.user = User.objects.create_user(
            email='consent-read@example.com',
            phone_number='+254700000002',
            password='testpass123',
        )
        self.consent = UserConsent.objects.create(
            user=self.user,
            consent_type=UserConsent.ConsentType.TERMS,
            version='v1.0',
            consented=True,
        )

    def test_serializer_returns_expected_fields(self):
        """Serializer returns all expected fields."""
        serializer = ConsentSerializer(self.consent)
        data = serializer.data

        expected_fields = {
            'id',
            'user',
            'consent_type',
            'consent_type_display',
            'version',
            'consented',
            'status',
            'timestamp',
        }
        self.assertEqual(set(data.keys()), expected_fields)

    def test_status_field_calls_model_method(self):
        """Status field calls the model's get_status method."""
        serializer = ConsentSerializer(self.consent)
        data = serializer.data
        self.assertEqual(data['status'], 'active')

    def test_consent_type_display_included(self):
        """Consent type display name is included."""
        serializer = ConsentSerializer(self.consent)
        data = serializer.data
        self.assertEqual(data['consent_type_display'], 'Terms')

    def test_false_consent_returns_never_given_status(self):
        """False consent returns 'never_given' status."""
        consent = UserConsent.objects.create(
            user=self.user,
            consent_type=UserConsent.ConsentType.MARKETING,
            version='v1.0',
            consented=False,
        )
        serializer = ConsentSerializer(consent)
        data = serializer.data
        self.assertEqual(data['status'], 'never_given')


class DataConsentLogSerializerTestCase(TestCase):
    """Test DataConsentLogSerializer read-only behavior."""

    def setUp(self):
        """Create test users and consent log."""
        self.user = User.objects.create_user(
            email='log-user@example.com',
            phone_number='+254700000003',
            password='testpass123',
        )
        self.admin = User.objects.create_user(
            email='log-admin@example.com',
            phone_number='+254700000004',
            password='testpass123',
            is_staff=True,
        )
        self.log = create_data_consent_log(
            user=self.user,
            accessed_by=self.admin,
            data_type='MEMBER_PROFILE',
            reason='Account review',
        )

    def test_serializer_returns_expected_fields(self):
        """Serializer returns all expected fields."""
        serializer = DataConsentLogSerializer(self.log)
        data = serializer.data

        expected_fields = {
            'id',
            'user',
            'user_email',
            'user_reference',
            'accessed_by',
            'accessed_by_email',
            'accessed_by_reference',
            'data_type',
            'reason',
            'timestamp',
        }
        self.assertEqual(set(data.keys()), expected_fields)

    def test_user_email_included_when_user_exists(self):
        """User email is included when user FK is not null."""
        serializer = DataConsentLogSerializer(self.log)
        data = serializer.data
        self.assertEqual(data['user_email'], self.user.email)

    def test_accessed_by_email_included_when_admin_exists(self):
        """Accessed_by email is included when FK is not null."""
        serializer = DataConsentLogSerializer(self.log)
        data = serializer.data
        self.assertEqual(data['accessed_by_email'], self.admin.email)

    def test_user_email_null_when_user_deleted(self):
        """User email is null when user FK is null."""
        # Delete the user
        self.user.delete()

        # Refresh log
        self.log.refresh_from_db()

        serializer = DataConsentLogSerializer(self.log)
        data = serializer.data
        self.assertIsNone(data['user_email'])
        self.assertIsNotNone(data['user_reference'])

    def test_accessed_by_email_null_when_admin_deleted(self):
        """Accessed_by email is null when FK is null."""
        # Delete the admin
        self.admin.delete()

        # Refresh log
        self.log.refresh_from_db()

        serializer = DataConsentLogSerializer(self.log)
        data = serializer.data
        self.assertIsNone(data['accessed_by_email'])
        self.assertIsNotNone(data['accessed_by_reference'])

    def test_snapshot_fields_preserved_after_deletion(self):
        """Snapshot fields are preserved even after user deletion."""
        # Store snapshot values
        user_ref_before = self.log.user_reference
        accessed_by_ref_before = self.log.accessed_by_reference

        # Delete both users
        self.user.delete()
        self.admin.delete()

        # Refresh log
        self.log.refresh_from_db()

        serializer = DataConsentLogSerializer(self.log)
        data = serializer.data

        self.assertEqual(data['user_reference'], user_ref_before)
        self.assertEqual(data['accessed_by_reference'], accessed_by_ref_before)
