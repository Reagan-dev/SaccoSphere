from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import User, UserDevice


class BiometricDeviceTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email='member@example.com',
            password='StrongPass1',
            first_name='Member',
            last_name='User',
            phone_number='254700000002',
        )
        self.other_user = User.objects.create_user(
            email='other@example.com',
            password='StrongPass1',
            first_name='Other',
            last_name='User',
            phone_number='254700000003',
        )
        self.client.force_authenticate(user=self.user)

    def test_register_device_creates_user_device(self):
        response = self.client.post(
            reverse('accounts:device-register'),
            {
                'device_id': 'ios-device-1',
                'device_name': 'iPhone 15 Pro',
                'platform': UserDevice.Platform.IOS,
                'push_token': 'push-token',
                'biometric_enabled': True,
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['device_registered'])
        device = UserDevice.objects.get(
            user=self.user,
            device_id='ios-device-1',
        )
        self.assertEqual(device.device_name, 'iPhone 15 Pro')
        self.assertEqual(device.platform, UserDevice.Platform.IOS)
        self.assertEqual(device.push_token, 'push-token')
        self.assertTrue(device.biometric_enabled)

    def test_register_device_updates_existing_user_device(self):
        UserDevice.objects.create(
            user=self.user,
            device_id='android-device-1',
            device_name='Old Android',
            platform=UserDevice.Platform.ANDROID,
            biometric_enabled=False,
        )

        response = self.client.post(
            reverse('accounts:device-register'),
            {
                'device_id': 'android-device-1',
                'device_name': 'Pixel 9',
                'platform': UserDevice.Platform.ANDROID,
                'push_token': 'new-push-token',
                'biometric_enabled': True,
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            UserDevice.objects.filter(
                user=self.user,
                device_id='android-device-1',
            ).count(),
            1,
        )
        device = UserDevice.objects.get(
            user=self.user,
            device_id='android-device-1',
        )
        self.assertEqual(device.device_name, 'Pixel 9')
        self.assertEqual(device.push_token, 'new-push-token')
        self.assertTrue(device.biometric_enabled)

    def test_list_devices_returns_only_authenticated_users_devices(self):
        UserDevice.objects.create(
            user=self.user,
            device_id='owned-device',
            device_name='Owned Device',
            platform=UserDevice.Platform.IOS,
            biometric_enabled=True,
        )
        UserDevice.objects.create(
            user=self.other_user,
            device_id='other-device',
            device_name='Other Device',
            platform=UserDevice.Platform.ANDROID,
            biometric_enabled=True,
        )

        response = self.client.get(reverse('accounts:device-list'))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        device_ids = {device['device_id'] for device in response.data}
        self.assertEqual(device_ids, {'owned-device'})
        self.assertEqual(
            set(response.data[0].keys()),
            {
                'device_id',
                'device_name',
                'platform',
                'biometric_enabled',
                'last_seen',
            },
        )

    def test_revoke_device_deletes_only_own_device(self):
        UserDevice.objects.create(
            user=self.user,
            device_id='owned-device',
            platform=UserDevice.Platform.IOS,
        )

        response = self.client.delete(
            reverse(
                'accounts:device-revoke',
                kwargs={'device_id': 'owned-device'},
            ),
        )

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(
            UserDevice.objects.filter(
                user=self.user,
                device_id='owned-device',
            ).exists()
        )

    def test_me_response_reports_biometric_login_enabled(self):
        UserDevice.objects.create(
            user=self.user,
            device_id='owned-device',
            platform=UserDevice.Platform.IOS,
            biometric_enabled=True,
        )

        response = self.client.get(reverse('accounts:me'))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['data']['biometric_login_enabled'])

    def test_token_refresh_accepts_refresh_token_for_biometric_flow(self):
        self.client.force_authenticate(user=None)
        refresh = RefreshToken.for_user(self.user)

        response = self.client.post(
            reverse('accounts:token-refresh'),
            {'refresh': str(refresh)},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('access', response.data)
