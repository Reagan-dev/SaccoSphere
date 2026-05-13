"""Tests for SACCO admin dashboard API views."""

from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models import Sacco, User
from saccomanagement.models import Role
from saccomembership.models import Membership, SaccoApplication


class SaccoAdminDashboardViewsTestCase(TestCase):
    """Test SACCO-scoped admin dashboard endpoints."""

    def setUp(self):
        """Create SACCOs, admin role, and API client."""
        self.client = APIClient()
        self.sacco = Sacco.objects.create(
            name='Alpha SACCO',
            registration_number='ALPHA001',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
        )
        self.other_sacco = Sacco.objects.create(
            name='Beta SACCO',
            registration_number='BETA001',
            sector=Sacco.Sector.TECHNOLOGY,
            county='Kiambu',
        )
        self.admin = User.objects.create_user(
            email='admin@example.com',
            password='StrongPass123',
            first_name='Admin',
            last_name='User',
        )
        Role.objects.create(
            user=self.admin,
            sacco=self.sacco,
            name=Role.SACCO_ADMIN,
        )
        self.client.force_authenticate(user=self.admin)

    def test_sacco_admin_sees_only_own_sacco_members(self):
        """Ensure a SACCO admin cannot retrieve another SACCO's member."""
        other_user = User.objects.create_user(
            email='other-member@example.com',
            password='StrongPass123',
            first_name='Other',
            last_name='Member',
        )
        other_membership = Membership.objects.create(
            user=other_user,
            sacco=self.other_sacco,
            status=Membership.Status.APPROVED,
            member_number='BETA-M001',
        )

        response = self.client.get(
            f'/api/v1/management/members/{other_membership.id}/',
            HTTP_X_SACCO_ID=str(self.sacco.id),
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_stats_correct_member_count(self):
        """Ensure stats count approved members in the admin's SACCO."""
        for index in range(5):
            user = User.objects.create_user(
                email=f'member-{index}@example.com',
                password='StrongPass123',
                first_name='Test',
                last_name=f'Member {index}',
            )
            Membership.objects.create(
                user=user,
                sacco=self.sacco,
                status=Membership.Status.APPROVED,
                member_number=f'ALPHA-M{index:03d}',
            )

        response = self.client.get(
            '/api/v1/management/stats/',
            HTTP_X_SACCO_ID=str(self.sacco.id),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['total_members'], 5)

    def test_application_approve_creates_membership(self):
        """Ensure approving an application creates an approved membership."""
        applicant = User.objects.create_user(
            email='applicant@example.com',
            password='StrongPass123',
            first_name='App',
            last_name='Licant',
        )
        application = SaccoApplication.objects.create(
            user=applicant,
            sacco=self.sacco,
            status=SaccoApplication.Status.SUBMITTED,
        )

        response = self.client.patch(
            f'/api/v1/management/applications/{application.id}/review/',
            {
                'status': SaccoApplication.Status.APPROVED,
                'review_notes': 'Welcome aboard.',
            },
            format='json',
            HTTP_X_SACCO_ID=str(self.sacco.id),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(
            Membership.objects.filter(
                user=applicant,
                sacco=self.sacco,
                status=Membership.Status.APPROVED,
            ).exists()
        )
