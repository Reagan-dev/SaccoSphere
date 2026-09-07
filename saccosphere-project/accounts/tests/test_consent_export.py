"""Tests for the consent/audit-log data-export endpoint (ConsentExportView)."""

from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from accounts.models import User, UserConsent
from saccomanagement.models import DataConsentLog
from saccomanagement.odpc_logging import create_data_consent_log


EXPORT_URL = '/api/v1/accounts/consents/export/'


class ConsentExportViewTestCase(APITestCase):
    """Test ConsentExportView returns the authenticated user's own data only."""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            email='export-user@example.com',
            phone_number='+254700000020',
            password='testpass123',
        )
        self.other_user = User.objects.create_user(
            email='export-other@example.com',
            phone_number='+254700000021',
            password='testpass123',
        )
        self.admin = User.objects.create_user(
            email='export-admin@example.com',
            phone_number='+254700000022',
            password='testpass123',
            is_staff=True,
        )

    def test_unauthenticated_request_rejected(self):
        """Unauthenticated requests are rejected."""
        response = self.client.get(EXPORT_URL)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_authenticated_user_gets_own_consents_and_audit_logs(self):
        """A user's export includes their own consents and audit log entries."""
        UserConsent.objects.create(
            user=self.user,
            consent_type=UserConsent.ConsentType.TERMS,
            version='v1.0',
            consented=True,
        )
        UserConsent.objects.create(
            user=self.user,
            consent_type=UserConsent.ConsentType.MARKETING,
            version='v1.0',
            consented=True,
        )
        create_data_consent_log(
            user=self.user,
            accessed_by=self.admin,
            data_type='MEMBER_PROFILE',
            reason='Account review',
        )

        self.client.force_authenticate(user=self.user)
        response = self.client.get(EXPORT_URL)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()['data']
        self.assertIn('exported_at', data)
        self.assertEqual(data['consents']['count'], 2)
        self.assertEqual(len(data['consents']['results']), 2)
        self.assertEqual(data['audit_logs']['count'], 1)
        self.assertEqual(
            data['audit_logs']['results'][0]['data_type'],
            'MEMBER_PROFILE',
        )

    def test_export_excludes_other_users_consents(self):
        """A user's export never includes another user's consent records."""
        UserConsent.objects.create(
            user=self.other_user,
            consent_type=UserConsent.ConsentType.TERMS,
            version='v1.0',
            consented=True,
        )

        self.client.force_authenticate(user=self.user)
        response = self.client.get(EXPORT_URL)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()['data']
        self.assertEqual(data['consents']['count'], 0)
        self.assertEqual(data['consents']['results'], [])

    def test_export_only_includes_logs_about_this_user_not_logs_they_accessed(self):
        """Audit logs where this user was the accessor (not the subject) are excluded."""
        # This user, as an admin, accessed someone else's data - that log
        # entry is about other_user, not about self.user, and must not
        # appear in self.user's own export.
        create_data_consent_log(
            user=self.other_user,
            accessed_by=self.user,
            data_type='MEMBER_PROFILE',
            reason='Account review',
        )

        self.client.force_authenticate(user=self.user)
        response = self.client.get(EXPORT_URL)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()['data']
        self.assertEqual(data['audit_logs']['count'], 0)

    def test_user_id_query_param_is_ignored_cannot_target_another_user(self):
        """Attempting to target another user's export via a query param has no effect."""
        UserConsent.objects.create(
            user=self.other_user,
            consent_type=UserConsent.ConsentType.TERMS,
            version='v1.0',
            consented=True,
        )

        self.client.force_authenticate(user=self.user)
        response = self.client.get(
            EXPORT_URL,
            {
                'user_id': str(self.other_user.id),
                'user': str(self.other_user.id),
            },
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()['data']
        # Still scoped to self.user (the authenticated caller), not
        # other_user, regardless of the query params supplied.
        self.assertEqual(data['consents']['count'], 0)

    def test_export_is_paginated(self):
        """A user with more records than one page gets a paginated response."""
        for index in range(3):
            UserConsent.objects.create(
                user=self.user,
                consent_type=UserConsent.ConsentType.TERMS,
                version=f'v1.{index}',
                consented=True,
            )

        self.client.force_authenticate(user=self.user)
        response = self.client.get(
            EXPORT_URL,
            {'consents_page_size': 2},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()['data']
        self.assertEqual(data['consents']['count'], 3)
        self.assertEqual(len(data['consents']['results']), 2)
        self.assertIsNotNone(data['consents']['next'])
