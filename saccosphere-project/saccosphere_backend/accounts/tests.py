from django.urls import reverse
from rest_framework.test import APITestCase
from rest_framework import status
from .models import User, Sacco, Profile


class UserTests(APITestCase):
    def test_create_user(self):
        user = User.objects.create_user(
            email ="testuser@example.com",
            password ="testpass",
        )
        self.assertEqual(user.email, "testuser@example.com")
        self.assertTrue(user.check_password("testpass"))
        self.assertFalse(user.is_staff)

    def test_create_superuser(self):
        superuser = User.objects.create_superuser(
            email ="testsuperuser@example.com",
            password ="testpass",
        )
        self.assertEqual(superuser.email, "testsuperuser@example.com")
        self.assertTrue(superuser.check_password("testpass"))
        self.assertTrue(superuser.is_staff)

class RegisterUserAPITests(APITestCase):
    def test_register_user(self):
        url = reverse('register')
        data = {
            "email": "isaac@example.com",
            "first_name": "Isaac",
            "last_name": "Yevisa",
            "password": "Yevisa1234"
        }
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(User.objects.get().email, "isaac@example.com")

class LoginAPITests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="Antony@example.com",
            first_name="Antony",
            last_name="Muriuki",
            password="Muriuki1234"
        )

    def test_login_valid_user(self):
        url = reverse('login')
        data = {
            "email": "Antony@example.com",
            "password": "Muriuki1234"
        }
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('access', response.data['data'])
        self.assertIn('refresh', response.data['data'])

    def test_login_invalid_user(self):
        url = reverse('login')
        data = {
            "email": "wrong@example.com",
            "password": "wrongpassword"
        }
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('non_field_errors', response.data['errors'])

class LogoutAPITests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="Eugene@example.com",
            first_name="Eugene",
            last_name="kiplangat",
            password="Kiplangat1234"
        )
        login_url = reverse('login')
        response = self.client.post(login_url, {
            "email": "Eugene@example.com",
            "password": "Kiplangat1234"
        }, format='json')

        assert response.status_code == status.HTTP_200_OK, f"Login failed: {response.data}"
        self.access_token = response.data['data']['access']
        self.refresh_token = response.data['data']['refresh']

    def test_logout(self):
        url = reverse('logout')
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + self.access_token)
        data = {
            "refresh": self.refresh_token
        }
        response = self.client.post(url, data, format='json')
        print(response.status_code, response.data)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('message', response.data)

class SaccoModelTests(APITestCase):
    def setUp(self):
        self.sacco = Sacco.objects.create(
            name="Test Sacco",
            registration_number="REG123",
            email="sacco@example.com",
            website="http://www.testsacco.com",
            address="123 Sacco St",
            description="A test sacco",
            logo=None
        )
    def test_sacco_creation(self):
        self.assertEqual(self.sacco.name, "Test Sacco")
        self.assertEqual(self.sacco.registration_number, "REG123")
        self.assertEqual(self.sacco.email, "sacco@example.com")
        self.assertEqual(self.sacco.website, "http://www.testsacco.com")
        self.assertEqual(self.sacco.address, "123 Sacco St")
        self.assertEqual(self.sacco.description, "A test sacco")
        self.assertFalse(self.sacco.logo)

class SaccoAPITests(APITestCase):
    #create a test user
    def setUp(self):
        self.normal_user = User.objects.create_user(
            email="Ryann@example.com",
            first_name="Ryann",
            last_name="Kiptoo",
            password="Kiptoo1234"
        )
        self.admin_user = User.objects.create_superuser(
            email="Admin@example.com",
            first_name="Admin",
            last_name="User",
            password="Admin1234",
            is_staff=True,
            is_superuser=True
        )
        self.sacco = Sacco.objects.create(
            name="My Sacco",
            registration_number="REG001",
            email="mysacco@example.com"
        )

    def test_list_saccos(self):
        url = reverse('sacco-list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(len(response.data), 1)

    def test_normal_user_cannot_create_sacco(self):
        url = reverse('sacco-list')
        self.client.force_authenticate(user=self.normal_user)
        data = {
            "name": "Blocked Sacco",
            "registration_number": "REG002",
            "email": "blocked@example.com"
        }
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_can_create_sacco(self):
        url = reverse('sacco-list')
        self.client.force_authenticate(user=self.admin_user)
        data = {
            "name": "New Sacco",
            "registration_number": "REG003",
            "email": "newsacco@example.com"
        }
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Sacco.objects.count(), 2)

    def test_admin_can_update_sacco(self):
        url = reverse('sacco-detail', args=[self.sacco.id])
        self.client.force_authenticate(user=self.admin_user)
        data = {"name": "Updated Sacco"}
        response = self.client.patch(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.sacco.refresh_from_db()
        self.assertEqual(self.sacco.name, "Updated Sacco")

    def test_admin_can_delete_sacco(self):
        url = reverse('sacco-detail', args=[self.sacco.id])
        self.client.force_authenticate(user=self.admin_user)
        response = self.client.delete(url)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Sacco.objects.filter(id=self.sacco.id).exists())


# PROFILE TESTS
class ProfileModelTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="profile@example.com",
            password="profile123"
        )
        self.profile = Profile.objects.create(
            user=self.user,
            phone_number="123456789",
            bio="my name is reagan"
        )

    def test_profile_creation(self):
        self.assertEqual(self.profile.user.email, "profile@example.com")
        self.assertEqual(self.profile.bio, "my name is reagan")
        self.assertIsNotNone(self.profile.created_at)
