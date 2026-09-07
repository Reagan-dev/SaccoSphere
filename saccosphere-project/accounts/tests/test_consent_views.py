"""API-level tests for consent management views."""

from unittest.mock import patch

from django.conf import settings
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient, APITransactionTestCase

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

    def test_ip_captured_from_x_forwarded_for_behind_proxy(self):
        """Giving consent behind a reverse proxy records the real client IP,
        not a client-spoofed leading X-Forwarded-For entry.

        This deployment sits behind exactly one reverse proxy hop, which
        appends the true client IP as the last entry in the chain (see
        accounts/utils.py::get_client_ip) - '198.51.100.1' here simulates
        a value the client itself set, and '203.0.113.5' simulates what
        the proxy actually appended.
        """
        self.client.force_authenticate(user=self.user)
        data = {
            'consent_type': UserConsent.ConsentType.TERMS,
            'version': 'v1.0',
            'consented': True,
        }

        response = self.client.post(
            '/api/v1/accounts/consents/',
            data,
            format='json',
            HTTP_X_FORWARDED_FOR='198.51.100.1, 203.0.113.5',
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        consent = UserConsent.objects.get(id=response.data['id'])
        self.assertEqual(consent.ip_address, '203.0.113.5')

    def test_ip_captured_from_remote_addr_without_proxy(self):
        """Without an X-Forwarded-For header, REMOTE_ADDR is used directly."""
        self.client.force_authenticate(user=self.user)
        data = {
            'consent_type': UserConsent.ConsentType.PRIVACY,
            'version': 'v1.0',
            'consented': True,
        }

        response = self.client.post(
            '/api/v1/accounts/consents/',
            data,
            format='json',
            REMOTE_ADDR='192.0.2.9',
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        consent = UserConsent.objects.get(id=response.data['id'])
        self.assertEqual(consent.ip_address, '192.0.2.9')

    def test_no_duration_configured_leaves_expires_at_null(self):
        """With the real (empty) CONSENT_EXPIRY_DURATIONS, expires_at stays null."""
        self.assertEqual(settings.CONSENT_EXPIRY_DURATIONS, {})
        self.client.force_authenticate(user=self.user)

        data = {
            'consent_type': UserConsent.ConsentType.TERMS,
            'version': 'v1.0',
            'consented': True,
        }
        response = self.client.post('/api/v1/accounts/consents/', data, format='json')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        consent = UserConsent.objects.get(id=response.data['id'])
        self.assertIsNone(consent.expires_at)

    def test_configured_duration_sets_expires_at(self):
        """When a duration is configured for the type, expires_at is populated."""
        from datetime import timedelta

        from django.test import override_settings
        from django.utils import timezone

        self.client.force_authenticate(user=self.user)
        data = {
            'consent_type': UserConsent.ConsentType.MARKETING,
            'version': 'v1.0',
            'consented': True,
        }

        before = timezone.now()
        with override_settings(
            CONSENT_EXPIRY_DURATIONS={'MARKETING': timedelta(days=30)},
        ):
            response = self.client.post(
                '/api/v1/accounts/consents/', data, format='json',
            )
        after = timezone.now()

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        consent = UserConsent.objects.get(id=response.data['id'])
        self.assertIsNotNone(consent.expires_at)
        self.assertGreaterEqual(
            consent.expires_at, before + timedelta(days=30),
        )
        self.assertLessEqual(
            consent.expires_at, after + timedelta(days=30),
        )


class ConsentGiveRaceConditionTestCase(APITransactionTestCase):
    """
    Test the true database-level race path in ConsentGiveView.post - the
    except IntegrityError: branch, not the idempotency pre-check.

    Uses APITransactionTestCase (not TestCase) deliberately: this
    production view has no @transaction.atomic wrapping and
    settings.DATABASES has no ATOMIC_REQUESTS, so in real traffic each
    .create() runs as its own autocommit statement - an IntegrityError
    there does not poison anything else. Django's plain TestCase wraps
    every test body in one atomic block for fast rollback-based isolation,
    which does NOT match that: under TestCase, this same scenario raises
    TransactionManagementError on the recovery query, because the IntegrityError
    poisons the test's own wrapping transaction. That would be a test
    artifact, not a real bug - APITransactionTestCase (no such wrapping,
    real autocommit, slower table-truncation isolation) is what actually
    reflects production behavior here.
    """

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            email='race-api@example.com',
            phone_number='+254700000022',
            password='testpass123',
        )

    def test_race_condition_at_database_level_recovers_without_500(self):
        """A create() that collides with a just-committed row (simulating a
        true race where this request's own idempotency pre-check missed,
        because the other request committed in the gap right after) is
        recovered via the IntegrityError handler, not surfaced as a 500.
        """
        consent_type = UserConsent.ConsentType.MARKETING
        version = 'v1.0'

        # The other, winning concurrent request: already committed.
        winner = UserConsent.objects.create(
            user=self.user,
            consent_type=consent_type,
            version=version,
            consented=True,
        )

        self.client.force_authenticate(user=self.user)
        data = {
            'consent_type': consent_type,
            'version': version,
            'consented': True,
        }

        real_filter = UserConsent.objects.filter
        calls = {'count': 0}

        def flaky_filter(*args, **kwargs):
            calls['count'] += 1
            if calls['count'] == 1:
                # The view's idempotency pre-check - force a miss, as if
                # it ran just before "winner" was committed.
                return UserConsent.objects.none()
            return real_filter(*args, **kwargs)

        with patch.object(
            UserConsent.objects, 'filter', side_effect=flaky_filter,
        ):
            response = self.client.post(
                '/api/v1/accounts/consents/', data, format='json',
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(str(response.data['id']), str(winner.id))
        self.assertEqual(
            UserConsent.objects.filter(
                user=self.user, consent_type=consent_type, version=version,
            ).count(),
            1,
        )


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


class WithdrawThenGiveAgainTestCase(TestCase):
    """
    Test that re-giving consent for the same (consent_type, version) after
    a withdrawal produces a new active record, not a silent no-op.

    Found during final QA: the give-consent unique constraint used to
    apply regardless of withdrawn_at, so re-giving the exact same version
    after withdrawal hit the constraint on the withdrawn row and fell into
    the IntegrityError recovery path, which (before this fix) re-fetched
    the *withdrawn* row and returned it as if the request had succeeded -
    the caller saw HTTP 200 with what looked like a normal consent object,
    but get_consent_status still reported 'withdrawn'. The constraint is
    now scoped to withdrawn_at__isnull=True (accounts/migrations/0022),
    and the recovery path's re-fetch is scoped the same way.
    """

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            email='regive-api@example.com',
            phone_number='+254700000021',
            password='testpass123',
        )

    def test_give_after_withdraw_creates_new_active_record(self):
        """Re-giving the same version after withdrawal succeeds and reactivates."""
        self.client.force_authenticate(user=self.user)
        data = {
            'consent_type': UserConsent.ConsentType.MARKETING,
            'version': 'v1.0',
            'consented': True,
        }

        first_response = self.client.post(
            '/api/v1/accounts/consents/', data, format='json',
        )
        self.assertEqual(first_response.status_code, status.HTTP_201_CREATED)
        first_id = first_response.data['id']

        withdraw_response = self.client.post(
            f'/api/v1/accounts/consents/{UserConsent.ConsentType.MARKETING}/withdraw/',
            format='json',
        )
        self.assertEqual(withdraw_response.status_code, status.HTTP_200_OK)

        second_response = self.client.post(
            '/api/v1/accounts/consents/', data, format='json',
        )

        # A genuinely new active record, not the withdrawn one echoed back.
        self.assertEqual(second_response.status_code, status.HTTP_201_CREATED)
        second_id = second_response.data['id']
        self.assertNotEqual(first_id, second_id)
        self.assertEqual(second_response.data['status'], 'active')

        from accounts.services.consent import get_consent_status, has_active_consent

        self.assertEqual(
            get_consent_status(self.user, UserConsent.ConsentType.MARKETING),
            'active',
        )
        self.assertTrue(
            has_active_consent(self.user, UserConsent.ConsentType.MARKETING)
        )

    def test_history_preserves_both_the_withdrawal_and_the_regive(self):
        """History shows both records - nothing is lost or overwritten.

        Note: ConsentSerializer.status reports the consent_type's *current*
        status (via get_status -> get_consent_status, which always looks up
        the user's latest record for that type), not this specific row's
        own state - so every entry in a history listing shows the same
        status value. That's an existing, deliberate design from the
        status-field prompt, not something this test changes. To prove
        each row's own historical state is genuinely preserved (not
        collapsed into one row), this checks withdrawn_at directly via the
        ORM rather than the serialized 'status' field.
        """
        self.client.force_authenticate(user=self.user)
        data = {
            'consent_type': UserConsent.ConsentType.MARKETING,
            'version': 'v1.0',
            'consented': True,
        }

        first_id = self.client.post(
            '/api/v1/accounts/consents/', data, format='json',
        ).data['id']
        self.client.post(
            f'/api/v1/accounts/consents/{UserConsent.ConsentType.MARKETING}/withdraw/',
            format='json',
        )
        second_id = self.client.post(
            '/api/v1/accounts/consents/', data, format='json',
        ).data['id']

        history_response = self.client.get(
            '/api/v1/accounts/consents/history/', format='json',
        )

        self.assertEqual(history_response.status_code, status.HTTP_200_OK)
        record_ids = {item['id'] for item in history_response.data}
        self.assertIn(first_id, record_ids)
        self.assertIn(second_id, record_ids)
        self.assertEqual(len(history_response.data), 2)

        first_record = UserConsent.objects.get(id=first_id)
        second_record = UserConsent.objects.get(id=second_id)
        self.assertIsNotNone(first_record.withdrawn_at)
        self.assertIsNone(second_record.withdrawn_at)


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

    def test_stale_version_reports_outdated_not_active_and_blocks_enforcement(self):
        """A consent recorded against an old policy version is 'outdated', not
        'active', both via the API and via has_active_consent - so
        require_consent-style enforcement correctly treats it as not consented."""
        from accounts.services.consent import has_active_consent

        current_version = settings.CONSENT_POLICY_VERSIONS[
            UserConsent.ConsentType.TERMS
        ]
        self.assertNotEqual(current_version, 'v0.1')
        UserConsent.objects.create(
            user=self.user,
            consent_type=UserConsent.ConsentType.TERMS,
            version='v0.1',
            consented=True,
        )

        self.client.force_authenticate(user=self.user)
        response = self.client.get('/api/v1/accounts/consents/list/', format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        terms_entry = next(
            item for item in response.data
            if item['consent_type'] == UserConsent.ConsentType.TERMS
        )
        self.assertEqual(terms_entry['status'], 'outdated')
        self.assertNotEqual(terms_entry['status'], 'active')

        # Same fixture, same consent_type: the enforcement-facing function
        # must agree with what the API just reported.
        self.assertFalse(
            has_active_consent(self.user, UserConsent.ConsentType.TERMS)
        )


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
