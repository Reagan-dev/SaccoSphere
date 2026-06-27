from unittest.mock import patch

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import KYCVerification, User


class GoogleOAuthTest(APITestCase):
    def setUp(self):
        self.url = reverse('accounts:google-oauth-callback')
        self.known_user = User.objects.create_user(
            email='known@example.com',
            password='StrongPass1',
            first_name='Known',
            last_name='User',
        )

    def _google_payload(self, email):
        return {
            'email': email,
            'given_name': 'Google',
            'family_name': 'User',
            'name': 'Google User',
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
    def test_signup_flow_with_existing_email_returns_tokens(
        self,
        verify_token,
    ):
        verify_token.return_value = self._google_payload(
            self.known_user.email,
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
    def test_login_flow_works_for_known_user(self, verify_token):
        verify_token.return_value = self._google_payload(
            self.known_user.email,
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
