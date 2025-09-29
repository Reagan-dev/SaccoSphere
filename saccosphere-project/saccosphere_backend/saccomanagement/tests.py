from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase
from django.contrib.auth import get_user_model
from accounts.models import Sacco
from saccomanagement.models import Management

User = get_user_model()


class ManagementTests(APITestCase):

    def setUp(self):
        # Create an admin user
        self.admin_user = User.objects.create_user(
            email="admin@example.com",
            password="AdminPass123",
            first_name="Admin",
            last_name="User",
            is_staff=True
        )

        # Create a normal user
        self.normal_user = User.objects.create_user(
            email="user@example.com",
            password="UserPass123",
            first_name="Normal",
            last_name="User"
        )

        # Create a test Sacco
        self.sacco = Sacco.objects.create(
            name="Test Sacco",
            registration_number="SAC12345",
            description="A test sacco"
        )

        # Login admin and store token
        login_url = reverse("login")
        response = self.client.post(login_url, {
            "email": "admin@example.com",
            "password": "AdminPass123"
        }, format="json")
        self.admin_access = response.data["data"]["access"]

        # Login normal user and store token
        response = self.client.post(login_url, {
            "email": "user@example.com",
            "password": "UserPass123"
        }, format="json")
        self.user_access = response.data["data"]["access"]

        # URL for management list/create
        self.management_url = reverse("management-list")

    def test_admin_can_create_management(self):
        # Authenticate as admin
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + self.admin_access)

        data = {
            "sacco": str(self.sacco.id),
            "management": "verified"
        }
        response = self.client.post(self.management_url, data, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["management"], "verified")

    def test_normal_user_cannot_create_management(self):
        # Authenticate as normal user
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + self.user_access)

        data = {
            "sacco": str(self.sacco.id),
            "management": "verified"
        }
        response = self.client.post(self.management_url, data, format="json")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_can_update_management_status(self):
        # Create management record as admin
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + self.admin_access)
        mgmt = Management.objects.create(sacco=self.sacco, management="verified")

        # Call custom action to update status
        url = reverse("management-set-status", kwargs={"pk": mgmt.id})
        response = self.client.post(url, {"management": "updated"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["management"], "updated")

    def test_invalid_status_rejected(self):
        # Create management record
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + self.admin_access)
        mgmt = Management.objects.create(sacco=self.sacco, management="verified")

        # Try invalid status
        url = reverse("management-set-status", kwargs={"pk": mgmt.id})
        response = self.client.post(url, {"management": "invalid_status"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("detail", response.data)

    def test_admin_can_list_management(self):
        # Create management record
        Management.objects.create(sacco=self.sacco, management="verified")

        # Authenticate as admin
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + self.admin_access)

        response = self.client.get(self.management_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(len(response.data), 1)

    def test_normal_user_cannot_list_management_if_not_member(self):
        # Authenticate as normal user
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + self.user_access)

        response = self.client.get(self.management_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Should return empty list because user is not in any sacco
        self.assertEqual(len(response.data), 0)


