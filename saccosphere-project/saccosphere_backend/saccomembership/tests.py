from django.urls import reverse
from rest_framework.test import APITestCase
from rest_framework import status
from django.contrib.auth import get_user_model
from accounts.models import Sacco
from saccomembership.models import Membership

User = get_user_model()


class MembershipTests(APITestCase):

    def setUp(self):
        # Create a regular user
        self.user = User.objects.create_user(
            email="member@example.com",
            first_name="Test",
            last_name="Member",
            password="StrongPass123"
        )

        # Create an admin user (staff)
        self.admin = User.objects.create_superuser(
            email="admin@example.com",
            first_name="Admin",
            last_name="User",
            password="AdminPass123"
        )

        # Create a test Sacco
        self.sacco = Sacco.objects.create(
            name="Test Sacco",
            registration_number="SAC12345"
        )

        # Get the membership endpoint
        self.membership_url = reverse('membership-list')

    def test_create_membership(self):
        # Authenticate as a normal user
        self.client.force_authenticate(user=self.user)

        # Data for creating membership
        data = {"sacco": self.sacco.id}

        # Make POST request
        response = self.client.post(self.membership_url, data, format='json')

        # Assert membership created successfully
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Membership.objects.count(), 1)
        self.assertEqual(Membership.objects.first().user, self.user)

    def test_duplicate_membership_not_allowed(self):
        # Authenticate as a normal user
        self.client.force_authenticate(user=self.user)

        # Create first membership
        Membership.objects.create(user=self.user, sacco=self.sacco)

        # Try creating the same membership again
        data = {"sacco": self.sacco.id}
        response = self.client.post(self.membership_url, data, format='json')

        # Assert validation error returned
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("This user is already a member of the Sacco.", response.data['non_field_errors'][0])
    
    def test_admin_can_approve_membership(self):
        # Create a pending membership
        membership = Membership.objects.create(user=self.user, sacco=self.sacco)

        # Authenticate as admin
        self.client.force_authenticate(user=self.admin)

        # Build approve URL
        url = reverse('membership-approve', args=[membership.id])

        # Post approve request
        response = self.client.post(url)

        # Assert membership approved
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        membership.refresh_from_db()
        self.assertEqual(membership.status, 'approved')
        self.assertTrue(membership.is_active)

    def test_admin_can_reject_membership(self):
        # Create a pending membership
        membership = Membership.objects.create(user=self.user, sacco=self.sacco)

        # Authenticate as admin
        self.client.force_authenticate(user=self.admin)

        # Build reject URL
        url = reverse('membership-reject', args=[membership.id])

        # Post reject request
        response = self.client.post(url)

        # Assert membership rejected
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        membership.refresh_from_db()
        self.assertEqual(membership.status, 'rejected')
        self.assertFalse(membership.is_active)

    def test_member_can_leave_sacco(self):
        # Create and approve a membership
        membership = Membership.objects.create(
            user=self.user, sacco=self.sacco, status="approved", is_active=True
        )

        # Authenticate as the member
        self.client.force_authenticate(user=self.user)

        # Build leave URL
        url = reverse('membership-leave', args=[membership.id])

        # Post leave request
        response = self.client.post(url)

        # Assert member successfully left
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        membership.refresh_from_db()
        self.assertEqual(membership.status, 'left')
        self.assertFalse(membership.is_active)

    def test_non_owner_cannot_leave_membership(self):
        # Create membership for self.user
        membership = Membership.objects.create(
            user=self.user, sacco=self.sacco, status="approved", is_active=True
        )

        # Authenticate as another random user
        other_user = User.objects.create_user(
            email="other@example.com",
            first_name="Other",
            last_name="User",
            password="OtherPass123"
        )
        self.client.force_authenticate(user=other_user)

        # Try leaving someone else's membership
        url = reverse('membership-leave', args=[membership.id])
        response = self.client.post(url)

        # Assert forbidden response
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)



