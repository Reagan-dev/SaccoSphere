"""Tests for LoginView (POST /api/v1/accounts/login/).

No test previously exercised this endpoint directly - every other test
needing an authenticated user goes through APIClient.force_authenticate,
which bypasses LoginView entirely. That gap let a real bug ship invisibly:
LoginView.post called get_user_sacco_context(user) without importing it,
so every real login attempt raised NameError and returned HTTP 500.
"""

from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from accounts.models import Sacco, User
from saccomanagement.models import Role


LOGIN_URL = '/api/v1/accounts/login/'


class LoginViewTestCase(APITestCase):
    """Test the real login endpoint end to end."""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            email='login-test@example.com',
            phone_number='+254700000070',
            password='StrongPass123',
        )

    def test_successful_login_returns_tokens_and_sacco_context(self):
        """A valid login succeeds and includes tokens plus sacco context."""
        response = self.client.post(
            LOGIN_URL,
            {'email': self.user.email, 'password': 'StrongPass123'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data['data']
        self.assertIn('access', data)
        self.assertIn('refresh', data)
        self.assertIn('sacco_context', data)
        self.assertIn('sacco_id', data)

    def test_member_with_no_roles_gets_null_sacco_context(self):
        """A plain member (no Role rows) gets sacco_id=None, role=MEMBER."""
        response = self.client.post(
            LOGIN_URL,
            {'email': self.user.email, 'password': 'StrongPass123'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data['data']
        self.assertIsNone(data['sacco_id'])
        self.assertEqual(data['sacco_context']['role'], Role.MEMBER)
        self.assertFalse(data['sacco_context']['is_sacco_admin'])

    def test_sacco_admin_login_includes_sacco_id(self):
        """A SACCO_ADMIN's login response includes their sacco's id."""
        sacco = Sacco.objects.create(
            name='Login Test SACCO',
            registration_number='LOGIN001',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
        )
        Role.objects.create(user=self.user, sacco=sacco, name=Role.SACCO_ADMIN)

        response = self.client.post(
            LOGIN_URL,
            {'email': self.user.email, 'password': 'StrongPass123'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data['data']
        self.assertEqual(data['sacco_id'], str(sacco.id))
        self.assertTrue(data['sacco_context']['is_sacco_admin'])

    def test_invalid_password_rejected(self):
        """Wrong password returns 401, not a 500."""
        response = self.client.post(
            LOGIN_URL,
            {'email': self.user.email, 'password': 'WrongPassword'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_nonexistent_email_rejected(self):
        """A login attempt for an unknown email returns 401, not a 500."""
        response = self.client.post(
            LOGIN_URL,
            {'email': 'nobody@example.com', 'password': 'WhateverPass123'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
