"""Google OAuth tests."""
import hmac
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch, MagicMock
import importlib

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from accounts.models import KYCVerification, User
from accounts.oauth_views import NONCE_TTL_SECONDS

class GoogleOAuthTest(APITestCase):
    """Regression tests for existing OAuth functionality."""
    
    def setUp(self):
        self.url = reverse('accounts:google-oauth-callback')
        self.known_user = User.objects.create_user(
            email='known@example.com',
            password='StrongPass1',
            first_name='Known',
            last_name='User',
        )
        # Disable throttling for these tests
        from accounts.oauth_views import GoogleOAuthCallbackView
        GoogleOAuthCallbackView.throttle_classes = []

    def _google_payload(self, email, email_verified=True, sub='google-sub-123'):
        return {
            'email': email,
            'given_name': 'Google',
            'family_name': 'User',
            'name': 'Google User',
            'email_verified': email_verified,
            'sub': sub,
        }

    @patch('accounts.oauth_views.verify_google_id_token')
    def test_login_flow_rejects_unknown_user(self, verify_token):
        verify_token.return_value = self._google_payload('new@example.com')

        response = self.client.post(
            self.url,
            {'id_token': 'valid-token', 'flow': 'login'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(
            response.data['error'],
            'No account found with this Google account. Please sign up first.',
        )

    @patch('accounts.oauth_views.verify_google_id_token')
    def test_signup_flow_creates_new_user(self, verify_token):
        verify_token.return_value = self._google_payload('new@example.com')

        response = self.client.post(
            self.url,
            {'id_token': 'valid-token', 'flow': 'signup'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertFalse(response.data['is_existing_user'])
        self.assertIn('access', response.data)
        self.assertIn('refresh', response.data)
        self.assertTrue(User.objects.filter(email='new@example.com').exists())
        self.assertTrue(
            KYCVerification.objects.filter(
                user__email='new@example.com',
                status=KYCVerification.Status.NOT_STARTED,
            ).exists()
        )

    @patch('accounts.oauth_views.verify_google_id_token')
    def test_signup_flow_with_existing_google_linked_user_returns_tokens(
        self,
        verify_token,
    ):
        # Create a user with Google already linked
        self.known_user.google_id = 'google-sub-123'
        self.known_user.save()
        
        verify_token.return_value = self._google_payload(
            self.known_user.email,
            sub='google-sub-123',
        )

        response = self.client.post(
            self.url,
            {'id_token': 'valid-token', 'flow': 'signup'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['is_existing_user'])
        self.assertEqual(
            response.data['message'],
            'Account already exists — you have been logged in.',
        )
        self.assertIn('access', response.data)
        self.assertIn('refresh', response.data)

    @patch('accounts.oauth_views.verify_google_id_token')
    def test_login_flow_works_for_google_linked_user(self, verify_token):
        # Create a user with Google already linked
        self.known_user.google_id = 'google-sub-123'
        self.known_user.save()
        
        verify_token.return_value = self._google_payload(
            self.known_user.email,
            sub='google-sub-123',
        )

        response = self.client.post(
            self.url,
            {'id_token': 'valid-token', 'flow': 'login'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('access', response.data)
        self.assertIn('refresh', response.data)
        self.assertEqual(response.data['user']['email'], self.known_user.email)


class GoogleOAuthStartupSafetyTest(APITestCase):
    """Test startup safety check for OAUTH_MOCK."""
    
    def test_oauth_mock_true_with_debug_false_raises_improperly_configured(self):
        """Test that OAUTH_MOCK=True with DEBUG=False raises ImproperlyConfigured."""
        with override_settings(DEBUG=False, OAUTH_MOCK=True):
            import accounts.apps
            config = accounts.apps.AccountsConfig('accounts', accounts.apps)
            
            with self.assertRaises(ImproperlyConfigured) as cm:
                config.ready()
            
            self.assertIn('OAUTH_MOCK=True is not allowed when DEBUG=False', str(cm.exception))


class GoogleOAuthAudienceValidationTest(APITestCase):
    """Test audience validation for multiple client IDs."""
    
    def setUp(self):
        self.url = reverse('accounts:google-oauth-callback')
        # Disable throttling for these tests
        from accounts.oauth_views import GoogleOAuthCallbackView
        GoogleOAuthCallbackView.throttle_classes = []

    def _google_payload(self, email, aud='client-id-1'):
        return {
            'email': email,
            'given_name': 'Google',
            'family_name': 'User',
            'name': 'Google User',
            'email_verified': True,
            'sub': 'google-sub-123',
            'aud': aud,
        }

    @override_settings(GOOGLE_OAUTH_ALLOWED_CLIENT_IDS=['client-id-1', 'client-id-2'])
    @patch('accounts.oauth_views.verify_google_id_token')
    def test_token_with_allowed_audience_is_accepted(self, verify_token):
        verify_token.return_value = self._google_payload(
            'new@example.com',
            aud='client-id-1',
        )

        response = self.client.post(
            self.url,
            {'id_token': 'valid-token', 'flow': 'signup'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    @override_settings(GOOGLE_OAUTH_ALLOWED_CLIENT_IDS=['client-id-1', 'client-id-2'])
    @patch('accounts.oauth_views.verify_google_id_token')
    def test_token_with_disallowed_audience_is_rejected(self, verify_token):
        from rest_framework.exceptions import AuthenticationFailed
        # Mock to raise error for invalid audience
        verify_token.side_effect = AuthenticationFailed('Invalid Google token.')

        response = self.client.post(
            self.url,
            {'id_token': 'valid-token', 'flow': 'signup'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class GoogleOAuthNonceValidationTest(APITestCase):
    """Test nonce validation for replay protection."""

    def setUp(self):
        self.url = reverse('accounts:google-oauth-callback')
        self.link_url = reverse('accounts:google-oauth-link')
        # Disable throttling for these tests
        from accounts.oauth_views import GoogleOAuthCallbackView, GoogleOAuthLinkView
        GoogleOAuthCallbackView.throttle_classes = []
        GoogleOAuthLinkView.throttle_classes = []
        # Clear cache before each test
        cache.clear()

    def _google_payload(self, email, nonce='test-nonce', sub='google-sub-123'):
        return {
            'email': email,
            'given_name': 'Google',
            'family_name': 'User',
            'name': 'Google User',
            'email_verified': True,
            'sub': sub,
            'nonce': nonce,
        }

    @patch('accounts.oauth_views.verify_google_id_token')
    def test_matching_nonce_succeeds(self, verify_token):
        verify_token.return_value = self._google_payload('unique-match@example.com', nonce='test-nonce', sub='google-sub-match')

        response = self.client.post(
            self.url,
            {'id_token': 'valid-token', 'flow': 'signup', 'nonce': 'test-nonce'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    @patch('accounts.oauth_views.verify_google_id_token')
    def test_mismatched_nonce_returns_401(self, verify_token):
        verify_token.return_value = self._google_payload('new@example.com', nonce='token-nonce', sub='google-sub-mismatch')

        response = self.client.post(
            self.url,
            {'id_token': 'valid-token', 'flow': 'signup', 'nonce': 'request-nonce'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    @patch('accounts.oauth_views.verify_google_id_token')
    def test_nonce_replay_is_rejected(self, verify_token):
        """
        Test that nonce replay is rejected after first successful validation.

        This test confirms that the nonce state tracking prevents replay attacks.
        Once a nonce is successfully validated, subsequent attempts with the
        same nonce are rejected.
        """
        verify_token.return_value = self._google_payload('unique-replay@example.com', nonce='replay-nonce', sub='google-sub-replay')

        # First request with the nonce should succeed
        response1 = self.client.post(
            self.url,
            {'id_token': 'valid-token', 'flow': 'signup', 'nonce': 'replay-nonce'},
            format='json',
        )
        self.assertEqual(response1.status_code, status.HTTP_201_CREATED)

        # Replay the same nonce - should now be rejected
        response2 = self.client.post(
            self.url,
            {'id_token': 'valid-token', 'flow': 'signup', 'nonce': 'replay-nonce'},
            format='json',
        )
        # Replay should fail with 401
        self.assertEqual(response2.status_code, status.HTTP_401_UNAUTHORIZED)

    @patch('accounts.oauth_views.verify_google_id_token')
    def test_concurrent_nonce_validation_only_one_succeeds(self, verify_token):
        """
        Test that concurrent nonce validation with the same nonce
        results in only one request succeeding.

        This uses ThreadPoolExecutor to simulate concurrent replay attempts.
        The atomic cache.add() ensures only one request can consume the nonce.
        """
        verify_token.return_value = self._google_payload('unique-concurrent@example.com', nonce='concurrent-nonce', sub='google-sub-concurrent')

        def validate_nonce():
            """Helper function to validate nonce in a thread."""
            return self.client.post(
                self.url,
                {'id_token': 'valid-token', 'flow': 'signup', 'nonce': 'concurrent-nonce'},
                format='json',
            )

        # Fire 5 concurrent requests with the same nonce
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(validate_nonce) for _ in range(5)]
            responses = [future.result() for future in futures]

        # Count successes (201) and failures (401)
        success_count = sum(1 for r in responses if r.status_code == status.HTTP_201_CREATED)
        failure_count = sum(1 for r in responses if r.status_code == status.HTTP_401_UNAUTHORIZED)

        # Exactly one should succeed, the rest should fail
        self.assertEqual(success_count, 1, "Exactly one concurrent request should succeed")
        self.assertEqual(failure_count, 4, "Four concurrent requests should fail due to nonce replay")

    @override_settings(NONCE_REQUIRED=False)
    @patch('accounts.oauth_views.verify_google_id_token')
    def test_missing_nonce_succeeds_when_nonce_required_false(self, verify_token):
        verify_token.return_value = self._google_payload('unique-no-nonce@example.com', nonce=None, sub='google-sub-no-nonce')

        response = self.client.post(
            self.url,
            {'id_token': 'valid-token', 'flow': 'signup'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    @override_settings(NONCE_REQUIRED=True)
    @patch('accounts.oauth_views.verify_google_id_token')
    def test_missing_nonce_fails_when_nonce_required_true(self, verify_token):
        verify_token.return_value = self._google_payload('new@example.com', nonce=None, sub='google-sub-req-true')

        response = self.client.post(
            self.url,
            {'id_token': 'valid-token', 'flow': 'signup'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    @patch('accounts.oauth_views.verify_google_id_token')
    def test_link_view_matching_nonce_succeeds(self, verify_token):
        """Test that matching nonce is accepted in link view."""
        # Create a user without Google linked
        user = User.objects.create_user(
            email='link-test@example.com',
            password='testpass123',
        )
        self.client.force_authenticate(user=user)

        verify_token.return_value = self._google_payload('link-test@example.com', nonce='link-nonce', sub='google-sub-link')

        response = self.client.post(
            self.link_url,
            {'id_token': 'valid-token', 'nonce': 'link-nonce'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    @patch('accounts.oauth_views.verify_google_id_token')
    def test_link_view_mismatched_nonce_returns_401(self, verify_token):
        """Test that mismatched nonce is rejected in link view."""
        password_user = User.objects.create_user(
            email='password@example.com',
            password='StrongPass1',
        )
        verify_token.return_value = self._google_payload(
            password_user.email,
            nonce='token-nonce',
            sub='google-sub-link-mismatch',
        )

        self.client.force_authenticate(user=password_user)

        response = self.client.post(
            self.link_url,
            {'id_token': 'valid-token', 'nonce': 'request-nonce'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    @override_settings(NONCE_REQUIRED=True)
    @patch('accounts.oauth_views.verify_google_id_token')
    def test_link_view_missing_nonce_fails_when_nonce_required_true(self, verify_token):
        """Test that missing nonce is rejected in link view when NONCE_REQUIRED=True."""
        password_user = User.objects.create_user(
            email='password@example.com',
            password='StrongPass1',
        )
        verify_token.return_value = self._google_payload(password_user.email, nonce=None, sub='google-sub-link-req-true')

        self.client.force_authenticate(user=password_user)

        response = self.client.post(
            self.link_url,
            {'id_token': 'valid-token'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class GoogleOAuthRateLimitingTest(APITestCase):
    """Test rate limiting on OAuth callback endpoint."""
    
    def setUp(self):
        self.url = reverse('accounts:google-oauth-callback')

    def _google_payload(self, email, sub='google-sub-123'):
        return {
            'email': email,
            'given_name': 'Google',
            'family_name': 'User',
            'name': 'Google User',
            'email_verified': True,
            'sub': sub,
        }

    def test_throttle_class_is_applied_to_callback_view(self):
        """Test that GoogleOAuthThrottle is configured on the callback view."""
        from accounts.throttles import GoogleOAuthThrottle
        # Check the source code to verify the throttle is configured
        import accounts.oauth_views as oauth_views_module
        source = open(oauth_views_module.__file__).read()
        self.assertIn('GoogleOAuthThrottle', source)
        self.assertIn('throttle_classes', source)

    @patch('accounts.oauth_views.verify_google_id_token')
    def test_rate_limiting_is_configured(self, verify_token):
        """Test that rate limiting is properly configured."""
        from accounts.throttles import GoogleOAuthThrottle
        # Verify the throttle class exists and has the correct rate
        self.assertEqual(GoogleOAuthThrottle.rate, '10/minute')


class GoogleOAuthAccountLinkingTest(APITestCase):
    """Test account linking functionality."""
    
    def setUp(self):
        self.callback_url = reverse('accounts:google-oauth-callback')
        self.link_url = reverse('accounts:google-oauth-link')
        self.password_user = User.objects.create_user(
            email='password@example.com',
            password='StrongPass1',
            first_name='Password',
            last_name='User',
        )
        self.google_user = User.objects.create_user(
            email='google@example.com',
            password=None,
            first_name='Google',
            last_name='User',
            google_id='google-sub-456',
        )
        # Disable throttling for these tests
        from accounts.oauth_views import GoogleOAuthCallbackView, GoogleOAuthLinkView
        GoogleOAuthCallbackView.throttle_classes = []
        GoogleOAuthLinkView.throttle_classes = []

    def _google_payload(self, email, sub='google-sub-123'):
        return {
            'email': email,
            'given_name': 'Google',
            'family_name': 'User',
            'name': 'Google User',
            'email_verified': True,
            'sub': sub,
        }

    @patch('accounts.oauth_views.verify_google_id_token')
    def test_password_only_account_returns_409_on_login(self, verify_token):
        verify_token.return_value = self._google_payload(self.password_user.email)

        response = self.client.post(
            self.callback_url,
            {'id_token': 'valid-token', 'flow': 'login'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data['code'], 'account_exists_password_only')

    @patch('accounts.oauth_views.verify_google_id_token')
    def test_password_only_account_returns_409_on_signup(self, verify_token):
        verify_token.return_value = self._google_payload(self.password_user.email)

        response = self.client.post(
            self.callback_url,
            {'id_token': 'valid-token', 'flow': 'signup'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data['code'], 'account_exists_password_only')

    @patch('accounts.oauth_views.verify_google_id_token')
    def test_link_endpoint_succeeds_for_matching_verified_email(self, verify_token):
        verify_token.return_value = self._google_payload(self.password_user.email)

        # Authenticate as the password user
        self.client.force_authenticate(user=self.password_user)

        response = self.client.post(
            self.link_url,
            {'id_token': 'valid-token'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.password_user.refresh_from_db()
        self.assertEqual(self.password_user.google_id, 'google-sub-123')

    @patch('accounts.oauth_views.verify_google_id_token')
    def test_link_endpoint_rejects_mismatched_email(self, verify_token):
        verify_token.return_value = self._google_payload('different@example.com')

        self.client.force_authenticate(user=self.password_user)

        response = self.client.post(
            self.link_url,
            {'id_token': 'valid-token'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @patch('accounts.oauth_views.verify_google_id_token')
    def test_link_endpoint_rejects_already_linked_identity(self, verify_token):
        # Try to link the same Google identity to a different user
        verify_token.return_value = self._google_payload(
            self.password_user.email,
            sub='google-sub-456',  # Already linked to google_user
        )

        self.client.force_authenticate(user=self.password_user)

        response = self.client.post(
            self.link_url,
            {'id_token': 'valid-token'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data['code'], 'google_identity_already_linked')

    @patch('accounts.oauth_views.verify_google_id_token')
    def test_brand_new_email_still_signs_up_as_before(self, verify_token):
        verify_token.return_value = self._google_payload('brandnew@example.com', sub='google-sub-789')

        response = self.client.post(
            self.callback_url,
            {'id_token': 'valid-token', 'flow': 'signup'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertFalse(response.data['is_existing_user'])
        new_user = User.objects.get(email='brandnew@example.com')
        self.assertEqual(new_user.google_id, 'google-sub-789')

    @patch('accounts.oauth_views.verify_google_id_token')
    def test_linked_user_can_login_with_google(self, verify_token):
        # First link the account
        self.password_user.google_id = 'google-sub-999'
        self.password_user.save()

        verify_token.return_value = self._google_payload(
            self.password_user.email,
            sub='google-sub-999',
        )

        response = self.client.post(
            self.callback_url,
            {'id_token': 'valid-token', 'flow': 'login'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('access', response.data)


class GoogleOAuthRemovedCodeTest(APITestCase):
    """Test that authorization-code flow code has been removed."""
    
    def test_no_google_oauth_client_imports(self):
        """Confirm no view or URL references GoogleOAuthClient."""
        # Try to import the deleted module
        with self.assertRaises(ImportError):
            from accounts.integrations.oauth import GoogleOAuthClient
        
        # Check that oauth_views doesn't reference it
        import accounts.oauth_views as oauth_views_module
        oauth_views_source = importlib.import_module('accounts.oauth_views')
        source = open(oauth_views_module.__file__).read()
        self.assertNotIn('GoogleOAuthClient', source)
        self.assertNotIn('exchange_code_for_token', source)
        self.assertNotIn('get_user_info', source)


class GoogleOAuthLoggingTest(APITestCase):
    """Test logging with masked emails."""
    
    def setUp(self):
        self.url = reverse('accounts:google-oauth-callback')
        # Disable throttling for these tests
        from accounts.oauth_views import GoogleOAuthCallbackView
        GoogleOAuthCallbackView.throttle_classes = []

    def _google_payload(self, email):
        return {
            'email': email,
            'given_name': 'Google',
            'family_name': 'User',
            'name': 'Google User',
            'email_verified': True,
            'sub': 'google-sub-123',
        }

    @patch('accounts.oauth_views.verify_google_id_token')
    @patch('accounts.oauth_views.logger')
    def test_success_log_contains_masked_email(self, mock_logger, verify_token):
        verify_token.return_value = self._google_payload('john@example.com')

        response = self.client.post(
            self.url,
            {'id_token': 'valid-token', 'flow': 'signup'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        
        # Check that logger.info was called
        self.assertTrue(mock_logger.info.called)
        
        # Get the call arguments
        call_args = mock_logger.info.call_args
        extra = call_args[1].get('extra', {})
        
        # Assert masked email, not full email
        self.assertIn('masked_email', extra)
        self.assertEqual(extra['masked_email'], 'j***@example.com')
        self.assertNotIn('john@example.com', str(extra))

    @patch('accounts.oauth_views.verify_google_id_token')
    @patch('accounts.oauth_views.logger')
    def test_rejection_log_contains_masked_email(self, mock_logger, verify_token):
        # Create a password-only user
        password_user = User.objects.create_user(
            email='jane@example.com',
            password='StrongPass1',
        )
        
        verify_token.return_value = self._google_payload('jane@example.com')

        response = self.client.post(
            self.url,
            {'id_token': 'valid-token', 'flow': 'login'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        
        # Check that logger.warning was called
        self.assertTrue(mock_logger.warning.called)
        
        # Get the call arguments
        call_args = mock_logger.warning.call_args
        extra = call_args[1].get('extra', {})
        
        # Assert masked email, not full email
        self.assertIn('masked_email', extra)
        self.assertEqual(extra['masked_email'], 'j***@example.com')
        self.assertNotIn('jane@example.com', str(extra))

    @patch('accounts.oauth_views.verify_google_id_token')
    @patch('accounts.oauth_views.logger')
    def test_log_never_contains_id_token(self, mock_logger, verify_token):
        verify_token.return_value = self._google_payload('test@example.com')

        self.client.post(
            self.url,
            {'id_token': 'valid-token', 'flow': 'signup'},
            format='json',
        )

        # Check all logger calls
        for call in mock_logger.method_calls:
            call_str = str(call)
            self.assertNotIn('valid-token', call_str)
            self.assertNotIn('id_token', call_str)
