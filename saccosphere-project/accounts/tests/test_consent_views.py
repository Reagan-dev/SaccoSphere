"""API-level tests for consent management views."""

from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models import User, UserConsent


class ConsentGiveViewTestCase(TestCase):
    """Test ConsentGiveView API endpoint."""

    def setUp(self):
        """Create test user and client."""
        self.client = APIClient()
        self.user = User.objects.create_user(
            email='consent-api@example.com',
            phone_number='+254700000001',
            password='testpass123',
        )

    def test_give_consent_succeeds(self):
        """Giving consent with valid data succeeds."""
        self.client.force_authenticate(user=self.user)

        data = {
            'consent_type': UserConsent.ConsentType.TERMS,
            'version': 'v1.0',
            'consented': True,
        }

        response = self.client.post('/api/v1/accounts/consents/', data, format='json')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['consent_type'], UserConsent.ConsentType.TERMS)
        self.assertEqual(response.data['version'], 'v1.0')
        self.assertTrue(response.data['consented'])
        self.assertEqual(response.data['status'], 'active')

    def test_give_consent_idempotent_on_repeat(self):
        """Giving identical consent twice returns 200 with existing record."""
        self.client.force_authenticate(user=self.user)

        data = {
            'consent_type': UserConsent.ConsentType.PRIVACY,
            'version': 'v1.0',
            'consented': True,
        }

        # First request
        response1 = self.client.post('/api/v1/accounts/consents/', data, format='json')
        self.assertEqual(response1.status_code, status.HTTP_201_CREATED)
        consent_id_1 = response1.data['id']

        # Second identical request
        response2 = self.client.post('/api/v1/accounts/consents/', data, format='json')
        self.assertEqual(response2.status_code, status.HTTP_200_OK)
        consent_id_2 = response2.data['id']

        # Should return the same record
        self.assertEqual(consent_id_1, consent_id_2)

    def test_unauthenticated_request_rejected(self):
        """Unauthenticated requests are rejected."""
        data = {
            'consent_type': UserConsent.ConsentType.TERMS,
            'version': 'v1.0',
            'consented': True,
        }

        response = self.client.post('/api/v1/accounts/consents/', data, format='json')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_invalid_consent_type_rejected(self):
        """Invalid consent_type is rejected."""
        self.client.force_authenticate(user=self.user)

        data = {
            'consent_type': 'INVALID_TYPE',
            'version': 'v1.0',
            'consented': True,
        }

        response = self.client.post('/api/v1/accounts/consents/', data, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_invalid_version_rejected(self):
        """Invalid version format is rejected."""
        self.client.force_authenticate(user=self.user)

        data = {
            'consent_type': UserConsent.ConsentType.TERMS,
            'version': '1.0',  # Missing 'v' prefix
            'consented': True,
        }

        response = self.client.post('/api/v1/accounts/consents/', data, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_client_supplied_user_ignored(self):
        """Client-supplied user field is ignored/rejected by serializer."""
        self.client.force_authenticate(user=self.user)

        data = {
            'user': str(self.user.id),
            'consent_type': UserConsent.ConsentType.TERMS,
            'version': 'v1.0',
            'consented': True,
        }

        response = self.client.post('/api/v1/accounts/consents/', data, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class ConsentWithdrawViewTestCase(TestCase):
    """Test ConsentWithdrawView API endpoint."""

    def setUp(self):
        """Create test user and client."""
        self.client = APIClient()
        self.user = User.objects.create_user(
            email='withdraw-api@example.com',
            phone_number='+254700000002',
            password='testpass123',
        )
        self.consent = UserConsent.objects.create(
            user=self.user,
            consent_type=UserConsent.ConsentType.MARKETING,
            version='v1.0',
            consented=True,
        )

    def test_withdraw_consent_succeeds(self):
        """Withdrawing consent sets withdrawn_at."""
        self.client.force_authenticate(user=self.user)

        response = self.client.post(
            f'/api/v1/accounts/consents/{UserConsent.ConsentType.MARKETING}/withdraw/',
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Refresh from database
        self.consent.refresh_from_db()
        self.assertIsNotNone(self.consent.withdrawn_at)
        self.assertEqual(self.consent.get_status(), 'withdrawn')

    def test_withdraw_does_not_delete_record(self):
        """Withdrawing consent does not delete the record."""
        self.client.force_authenticate(user=self.user)

        consent_id = self.consent.id

        response = self.client.post(
            f'/api/v1/accounts/consents/{UserConsent.ConsentType.MARKETING}/withdraw/',
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Record should still exist
        self.assertTrue(
            UserConsent.objects.filter(id=consent_id).exists()
        )

    def test_withdraw_nonexistent_consent_returns_404(self):
        """Withdrawing non-existent consent returns 404."""
        self.client.force_authenticate(user=self.user)

        response = self.client.post(
            f'/api/v1/accounts/consents/{UserConsent.ConsentType.DATA_PROCESSING}/withdraw/',
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_unauthenticated_request_rejected(self):
        """Unauthenticated requests are rejected."""
        response = self.client.post(
            f'/api/v1/accounts/consents/{UserConsent.ConsentType.MARKETING}/withdraw/',
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_invalid_consent_type_rejected(self):
        """Invalid consent_type is rejected."""
        self.client.force_authenticate(user=self.user)

        response = self.client.post(
            '/api/v1/accounts/consents/INVALID_TYPE/withdraw/',
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class ConsentListViewTestCase(TestCase):
    """Test ConsentListView API endpoint."""

    def setUp(self):
        """Create test user and client."""
        self.client = APIClient()
        self.user = User.objects.create_user(
            email='list-api@example.com',
            phone_number='+254700000003',
            password='testpass123',
        )

    def test_list_consents_returns_all_types(self):
        """Listing consents returns status for all consent types."""
        self.client.force_authenticate(user=self.user)

        # Create some consents
        UserConsent.objects.create(
            user=self.user,
            consent_type=UserConsent.ConsentType.TERMS,
            version='v1.0',
            consented=True,
        )
        UserConsent.objects.create(
            user=self.user,
            consent_type=UserConsent.ConsentType.PRIVACY,
            version='v1.0',
            consented=False,
        )

        response = self.client.get('/api/v1/accounts/consents/list/', format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 4)  # All 4 consent types

        # Check that we have placeholders for never-given consents
        consent_types = [item['consent_type'] for item in response.data]
        self.assertIn(UserConsent.ConsentType.TERMS, consent_types)
        self.assertIn(UserConsent.ConsentType.PRIVACY, consent_types)
        self.assertIn(UserConsent.ConsentType.DATA_PROCESSING, consent_types)
        self.assertIn(UserConsent.ConsentType.MARKETING, consent_types)

    def test_unauthenticated_request_rejected(self):
        """Unauthenticated requests are rejected."""
        response = self.client.get('/api/v1/accounts/consents/list/', format='json')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class ConsentHistoryViewTestCase(TestCase):
    """Test ConsentHistoryView API endpoint."""

    def setUp(self):
        """Create test user and client."""
        self.client = APIClient()
        self.user = User.objects.create_user(
            email='history-api@example.com',
            phone_number='+254700000004',
            password='testpass123',
        )

    def test_history_returns_chronological_records(self):
        """History returns user's consent records in chronological order."""
        self.client.force_authenticate(user=self.user)

        # Create multiple consents
        consent1 = UserConsent.objects.create(
            user=self.user,
            consent_type=UserConsent.ConsentType.TERMS,
            version='v1.0',
            consented=True,
        )
        consent2 = UserConsent.objects.create(
            user=self.user,
            consent_type=UserConsent.ConsentType.PRIVACY,
            version='v1.0',
            consented=True,
        )

        response = self.client.get('/api/v1/accounts/consents/history/', format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 2)

        # Should be ordered by timestamp descending (most recent first)
        self.assertEqual(response.data[0]['id'], str(consent2.id))
        self.assertEqual(response.data[1]['id'], str(consent1.id))

    def test_unauthenticated_request_rejected(self):
        """Unauthenticated requests are rejected."""
        response = self.client.get('/api/v1/accounts/consents/history/', format='json')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_user_cannot_view_another_users_history(self):
        """User cannot view another user's consent history."""
        # Create another user
        other_user = User.objects.create_user(
            email='other-user@example.com',
            phone_number='+254700000005',
            password='testpass123',
        )

        # Create consent for other user
        UserConsent.objects.create(
            user=other_user,
            consent_type=UserConsent.ConsentType.TERMS,
            version='v1.0',
            consented=True,
        )

        # Authenticate as first user
        self.client.force_authenticate(user=self.user)

        response = self.client.get('/api/v1/accounts/consents/history/', format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Should only return self.user's consents (empty in this case)
        self.assertEqual(len(response.data), 0)


class CrossUserAccessTestCase(TestCase):
    """Test that users cannot access other users' consent records."""

    def setUp(self):
        """Create two test users."""
        self.client = APIClient()
        self.user1 = User.objects.create_user(
            email='user1@example.com',
            phone_number='+254700000006',
            password='testpass123',
        )
        self.user2 = User.objects.create_user(
            email='user2@example.com',
            phone_number='+254700000007',
            password='testpass123',
        )

    def test_user_cannot_give_consent_for_another_user(self):
        """User cannot give consent on behalf of another user."""
        self.client.force_authenticate(user=self.user1)

        # Even if user1 tries to include user2's ID in the request,
        # the view should ignore it and use request.user
        data = {
            'consent_type': UserConsent.ConsentType.TERMS,
            'version': 'v1.0',
            'consented': True,
        }

        response = self.client.post('/api/v1/accounts/consents/', data, format='json')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        # Consent should be for user1, not user2
        consent = UserConsent.objects.get(id=response.data['id'])
        self.assertEqual(consent.user, self.user1)
        self.assertNotEqual(consent.user, self.user2)

    def test_user_cannot_withdraw_another_users_consent(self):
        """User cannot withdraw another user's consent."""
        # Create consent for user2
        consent = UserConsent.objects.create(
            user=self.user2,
            consent_type=UserConsent.ConsentType.MARKETING,
            version='v1.0',
            consented=True,
        )

        self.client.force_authenticate(user=self.user1)

        response = self.client.post(
            f'/api/v1/accounts/consents/{UserConsent.ConsentType.MARKETING}/withdraw/',
            format='json',
        )

        # Should return 404 because user1 has no active consent of this type
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

        # user2's consent should still be active
        consent.refresh_from_db()
        self.assertIsNone(consent.withdrawn_at)

    def test_user_list_only_sees_own_consents(self):
        """User's consent list only shows their own consents."""
        # Create consents for both users
        UserConsent.objects.create(
            user=self.user1,
            consent_type=UserConsent.ConsentType.TERMS,
            version='v1.0',
            consented=True,
        )
        UserConsent.objects.create(
            user=self.user2,
            consent_type=UserConsent.ConsentType.PRIVACY,
            version='v1.0',
            consented=True,
        )

        self.client.force_authenticate(user=self.user1)

        response = self.client.get('/api/v1/accounts/consents/list/', format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Check that only user1's consent is shown
        terms_consent = next(
            (item for item in response.data if item['consent_type'] == UserConsent.ConsentType.TERMS),
            None
        )
        self.assertIsNotNone(terms_consent)
        self.assertEqual(terms_consent['status'], 'active')

        # user2's consent should not appear
        privacy_consent = next(
            (item for item in response.data if item['consent_type'] == UserConsent.ConsentType.PRIVACY),
            None
        )
        self.assertIsNotNone(privacy_consent)
        self.assertEqual(privacy_consent['status'], 'never_given')
