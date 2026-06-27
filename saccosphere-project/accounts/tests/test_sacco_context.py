"""Tests for SACCO context in login and profile responses."""

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import Sacco, User
from saccomanagement.models import Role
from saccomembership.models import Membership


class LoginSaccoContextTest(APITestCase):
    """Verify login returns sacco_context for each role type."""

    def setUp(self):
        self.sacco = Sacco.objects.create(
            name='Context SACCO',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
        )
        self.admin = User.objects.create_user(
            email='admin-context@example.com',
            password='StrongPass123',
            first_name='Admin',
            last_name='User',
        )
        Role.objects.create(
            user=self.admin,
            sacco=self.sacco,
            name=Role.SACCO_ADMIN,
        )

        self.member = User.objects.create_user(
            email='member-context@example.com',
            password='StrongPass123',
            first_name='Member',
            last_name='User',
        )
        Membership.objects.create(
            user=self.member,
            sacco=self.sacco,
            status=Membership.Status.APPROVED,
        )

        self.super_admin = User.objects.create_user(
            email='super-context@example.com',
            password='StrongPass123',
            first_name='Super',
            last_name='Admin',
        )
        Role.objects.create(
            user=self.super_admin,
            sacco=None,
            name=Role.SUPER_ADMIN,
        )

    def _login(self, email):
        return self.client.post(
            reverse('accounts:login'),
            {'email': email, 'password': 'StrongPass123'},
            format='json',
        )

    def test_sacco_admin_login_returns_sacco_id(self):
        response = self._login(self.admin.email)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        sacco_context = response.data['data']['sacco_context']
        self.assertTrue(sacco_context['is_sacco_admin'])
        self.assertEqual(sacco_context['role'], Role.SACCO_ADMIN)
        self.assertEqual(sacco_context['sacco_id'], str(self.sacco.id))
        self.assertEqual(sacco_context['sacco_name'], self.sacco.name)

    def test_member_login_returns_null_sacco_id(self):
        response = self._login(self.member.email)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        sacco_context = response.data['data']['sacco_context']
        self.assertFalse(sacco_context['is_sacco_admin'])
        self.assertEqual(sacco_context['role'], Role.MEMBER)
        self.assertIsNone(sacco_context['sacco_id'])
        self.assertIsNone(sacco_context['sacco_name'])

    def test_super_admin_login_returns_super_role(self):
        response = self._login(self.super_admin.email)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        sacco_context = response.data['data']['sacco_context']
        self.assertFalse(sacco_context['is_sacco_admin'])
        self.assertEqual(sacco_context['role'], Role.SUPER_ADMIN)
        self.assertIsNone(sacco_context['sacco_id'])


class MeEndpointSaccoContextTest(APITestCase):
    """Verify /accounts/me/ returns sacco_context."""

    def setUp(self):
        self.sacco = Sacco.objects.create(
            name='Me Context SACCO',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
        )
        self.admin = User.objects.create_user(
            email='me-admin@example.com',
            password='StrongPass123',
            first_name='Me',
            last_name='Admin',
        )
        Role.objects.create(
            user=self.admin,
            sacco=self.sacco,
            name=Role.SACCO_ADMIN,
        )
        self.client.force_authenticate(user=self.admin)

    def test_me_endpoint_returns_sacco_context(self):
        response = self.client.get(reverse('accounts:me'))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        payload = response.data['data']
        self.assertIn('sacco_context', payload)
        self.assertTrue(payload['sacco_context']['is_sacco_admin'])
        self.assertEqual(
            payload['sacco_context']['sacco_id'],
            str(self.sacco.id),
        )
        self.assertEqual(payload['sacco_context']['role'], Role.SACCO_ADMIN)
