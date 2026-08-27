"""End-to-end integration tests for Google OAuth flow."""

from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from rest_framework import status

from accounts.models import User, KYCVerification


class GoogleOAuthIntegrationTest(TestCase):
    """End-to-end tests for Google OAuth login/signup/link flows."""

    def setUp(self):
        """Set up test client and clear cache."""
        self.client = APIClient()
        cache.clear()
        # Disable throttling for these tests
        from accounts.oauth_views import GoogleOAuthCallbackView, GoogleOAuthLinkView
        GoogleOAuthCallbackView.throttle_classes = []
        GoogleOAuthLinkView.throttle_classes = []

    def _google_token_payload(
        self,
        email='test@example.com',
        sub='google-sub-123',
        email_verified=True,
        given_name='Test',
        family_name='User',
        nonce='test-nonce',
    ):
        """Return a realistic Google ID token payload."""
        return {
            'email': email,
            'sub': sub,
            'email_verified': email_verified,
            'given_name': given_name,
            'family_name': family_name,
            'name': f'{given_name} {family_name}',
            'nonce': nonce,
        }

    @patch('accounts.oauth_views.verify_google_id_token')
    def test_new_user_signup_creates_account_and_returns_tokens(self, verify_token):
        """
        Test that a brand-new user signing up via Google for the first time
        creates a User row with expected fields and returns valid session/token.
        """
        verify_token.return_value = self._google_token_payload(
            email='newuser@example.com',
            sub='google-sub-new',
            given_name='New',
            family_name='User',
        )

        response = self.client.post(
            '/api/v1/accounts/oauth/google/callback/',
            {
                'id_token': 'valid-google-token',
                'flow': 'signup',
                'nonce': 'test-nonce',
            },
            format='json',
        )

        # Check response status
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        # Check user was created
        user = User.objects.get(email='newuser@example.com')
        self.assertEqual(user.google_id, 'google-sub-new')
        self.assertEqual(user.first_name, 'New')
        self.assertEqual(user.last_name, 'User')
        # Google-only accounts have unusable passwords
        self.assertTrue(user.has_usable_password() is False)

        # Check KYCVerification was created
        kyc = KYCVerification.objects.get(user=user)
        self.assertEqual(kyc.status, KYCVerification.Status.NOT_STARTED)

        # Check tokens are returned
        self.assertIn('access', response.data)
        self.assertIn('refresh', response.data)
        self.assertIn('user', response.data)
        self.assertEqual(response.data['user']['email'], 'newuser@example.com')
        self.assertEqual(response.data['is_existing_user'], False)

    @patch('accounts.oauth_views.verify_google_id_token')
    def test_existing_google_linked_user_login_returns_tokens(self, verify_token):
        """
        Test that an existing, already-linked user logging in via Google
        returns the correct session/token without creating a duplicate user.
        """
        # Create a user with Google already linked
        user = User.objects.create_user(
            email='existing@example.com',
            password=None,
            first_name='Existing',
            last_name='User',
            google_id='google-sub-existing',
        )

        verify_token.return_value = self._google_token_payload(
            email='existing@example.com',
            sub='google-sub-existing',
            given_name='Existing',
            family_name='User',
        )

        response = self.client.post(
            '/api/v1/accounts/oauth/google/callback/',
            {
                'id_token': 'valid-google-token',
                'flow': 'login',
                'nonce': 'test-nonce',
            },
            format='json',
        )

        # Check response status
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Check no duplicate user was created
        self.assertEqual(User.objects.filter(email='existing@example.com').count(), 1)

        # Check tokens are returned for the correct user
        self.assertIn('access', response.data)
        self.assertIn('refresh', response.data)
        self.assertEqual(response.data['user']['email'], 'existing@example.com')

    @patch('accounts.oauth_views.verify_google_id_token')
    def test_authenticated_user_can_link_google_account(self, verify_token):
        """
        Test that an authenticated user can link their account to Google,
        and the link is recorded correctly.
        """
        # Create a password-only user
        user = User.objects.create_user(
            email='passworduser@example.com',
            password='testpass123',
            first_name='Password',
            last_name='User',
        )

        # Authenticate the user
        self.client.force_authenticate(user=user)

        verify_token.return_value = self._google_token_payload(
            email='passworduser@example.com',
            sub='google-sub-link',
            given_name='Password',
            family_name='User',
        )

        response = self.client.post(
            '/api/v1/accounts/oauth/google/link/',
            {
                'id_token': 'valid-google-token',
                'nonce': 'test-nonce',
            },
            format='json',
        )

        # Check response status
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data['message'],
            'Google account linked successfully.'
        )

        # Check user's google_id was updated
        user.refresh_from_db()
        self.assertEqual(user.google_id, 'google-sub-link')

    @patch('accounts.oauth_views.verify_google_id_token')
    def test_second_user_cannot_link_same_google_account(self, verify_token):
        """
        Test that a second user attempting to link the same Google account
        is rejected with a clear error.
        """
        # Create first user and link Google
        user1 = User.objects.create_user(
            email='user1@example.com',
            password='testpass123',
            google_id='google-sub-duplicate',
        )

        # Create second user
        user2 = User.objects.create_user(
            email='user2@example.com',
            password='testpass123',
        )

        # Authenticate second user
        self.client.force_authenticate(user=user2)

        verify_token.return_value = self._google_token_payload(
            email='user2@example.com',
            sub='google-sub-duplicate',  # Same Google account
            given_name='User',
            family_name='Two',
        )

        response = self.client.post(
            '/api/v1/accounts/oauth/google/link/',
            {
                'id_token': 'valid-google-token',
                'nonce': 'test-nonce',
            },
            format='json',
        )

        # Check response status
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(
            response.data['code'],
            'google_identity_already_linked'
        )
        self.assertIn('already linked', response.data['detail'])

        # Check second user's google_id was NOT updated
        user2.refresh_from_db()
        self.assertIsNone(user2.google_id)

    @patch('accounts.oauth_views.verify_google_id_token')
    def test_unverified_email_rejected_for_link_flow(self, verify_token):
        """
        Test that Google reporting email as unverified is rejected
        in the link flow with a clear error.
        """
        user = User.objects.create_user(
            email='unverified@example.com',
            password='testpass123',
        )

        self.client.force_authenticate(user=user)

        verify_token.return_value = self._google_token_payload(
            email='unverified@example.com',
            sub='google-sub-unverified',
            email_verified=False,  # Unverified email
            given_name='Unverified',
            family_name='User',
        )

        response = self.client.post(
            '/api/v1/accounts/oauth/google/link/',
            {
                'id_token': 'valid-google-token',
                'nonce': 'test-nonce',
            },
            format='json',
        )

        # Check response status
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertIn('not verified', response.data['error'])

        # Check user's google_id was NOT updated
        user.refresh_from_db()
        self.assertIsNone(user.google_id)

    @patch('accounts.oauth_views.verify_google_id_token')
    def test_unverified_email_accepted_for_new_user_signup(self, verify_token):
        """
        Test that Google reporting email as unverified is ACCEPTED
        for new user signup (business rule: email_verified not checked).
        """
        verify_token.return_value = self._google_token_payload(
            email='newunverified@example.com',
            sub='google-sub-new-unverified',
            email_verified=False,  # Unverified email
            given_name='New',
            family_name='Unverified',
        )

        response = self.client.post(
            '/api/v1/accounts/oauth/google/callback/',
            {
                'id_token': 'valid-google-token',
                'flow': 'signup',
                'nonce': 'test-nonce',
            },
            format='json',
        )

        # Check response status - should succeed
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        # Check user was created despite unverified email
        user = User.objects.get(email='newunverified@example.com')
        self.assertEqual(user.google_id, 'google-sub-new-unverified')

    @patch('accounts.oauth_views.verify_google_id_token')
    def test_unverified_email_rejected_for_password_only_login(self, verify_token):
        """
        Test that unverified email is rejected when logging in with
        a password-only account.
        """
        # Create password-only user
        user = User.objects.create_user(
            email='passwordonly@example.com',
            password='testpass123',
        )

        verify_token.return_value = self._google_token_payload(
            email='passwordonly@example.com',
            sub='google-sub-password',
            email_verified=False,
            given_name='Password',
            family_name='Only',
        )

        response = self.client.post(
            '/api/v1/accounts/oauth/google/callback/',
            {
                'id_token': 'valid-google-token',
                'flow': 'login',
                'nonce': 'test-nonce',
            },
            format='json',
        )

        # Check response status
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertIn('not verified', response.data['error'])

    @patch('accounts.oauth_views.verify_google_id_token')
    def test_email_mismatch_rejected_for_link_flow(self, verify_token):
        """
        Test that linking is rejected when Google email doesn't match
        the authenticated user's email.
        """
        user = User.objects.create_user(
            email='user@example.com',
            password='testpass123',
        )

        self.client.force_authenticate(user=user)

        verify_token.return_value = self._google_token_payload(
            email='different@example.com',  # Different email
            sub='google-sub-different',
            given_name='Different',
            family_name='Email',
        )

        response = self.client.post(
            '/api/v1/accounts/oauth/google/link/',
            {
                'id_token': 'valid-google-token',
                'nonce': 'test-nonce',
            },
            format='json',
        )

        # Check response status
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('does not match', response.data['error'])

        # Check user's google_id was NOT updated
        user.refresh_from_db()
        self.assertIsNone(user.google_id)
