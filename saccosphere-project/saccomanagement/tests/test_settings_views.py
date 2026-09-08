"""Tests for SaccoSettingsView: platform-admin access and bounds validation."""

from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models import Sacco, SaccoSettings, User
from saccomanagement.models import Role


class SaccoSettingsViewAccessTestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.sacco = Sacco.objects.create(
            name='Settings Access SACCO',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
        )
        self.other_sacco = Sacco.objects.create(
            name='Other Settings SACCO',
            sector=Sacco.Sector.FINANCE,
            county='Kisumu',
        )
        self.sacco_admin = User.objects.create_user(
            email='settings-sacco-admin@example.com',
            phone_number='+254700000300',
            password='testpass123',
        )
        Role.objects.create(
            user=self.sacco_admin, sacco=self.sacco, name=Role.SACCO_ADMIN,
        )
        self.super_admin = User.objects.create_user(
            email='settings-super-admin@example.com',
            phone_number='+254700000301',
            password='testpass123',
            is_staff=True,
        )
        # IsSaccoAdminOrSuperAdmin (the view's permission_classes) checks
        # only for an active SUPER_ADMIN Role row, not is_staff.
        Role.objects.create(
            user=self.super_admin, sacco=None, name=Role.SUPER_ADMIN,
        )
        self.url = reverse('management:sacco-settings')

    def test_super_admin_can_get_settings_for_a_specific_sacco_by_id(self):
        self.client.force_authenticate(user=self.super_admin)

        response = self.client.get(self.url, {'sacco_id': str(self.sacco.id)})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data['data']['sacco_id'], str(self.sacco.id),
        )

    def test_super_admin_can_patch_settings_for_a_specific_sacco_by_id(self):
        self.client.force_authenticate(user=self.super_admin)

        response = self.client.patch(
            f'{self.url}?sacco_id={self.other_sacco.id}',
            {'sms_daily_limit': 250},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(
            SaccoSettings.objects.filter(
                sacco=self.other_sacco, sms_daily_limit=250,
            ).exists(),
        )

    def test_super_admin_without_sacco_id_gets_clear_error(self):
        self.client.force_authenticate(user=self.super_admin)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_sacco_admin_still_reaches_only_their_own_current_sacco(self):
        self.client.force_authenticate(user=self.sacco_admin)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data['data']['sacco_id'], str(self.sacco.id),
        )

    def test_sacco_admin_cannot_reach_a_sacco_they_do_not_administer(self):
        self.client.force_authenticate(user=self.sacco_admin)

        response = self.client.get(
            self.url, HTTP_X_SACCO_ID=str(self.other_sacco.id),
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class SaccoSettingsBoundsValidationTestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.sacco = Sacco.objects.create(
            name='Bounds Validation SACCO',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
        )
        self.settings = SaccoSettings.objects.create(
            sacco=self.sacco,
            min_loan_amount=Decimal('1000.00'),
            max_loan_amount=Decimal('50000.00'),
            registration_fee=Decimal('500.00'),
            loan_multiplier=3,
        )
        self.admin = User.objects.create_user(
            email='bounds-admin@example.com',
            phone_number='+254700000310',
            password='testpass123',
        )
        Role.objects.create(
            user=self.admin, sacco=self.sacco, name=Role.SACCO_ADMIN,
        )
        self.client.force_authenticate(user=self.admin)
        self.url = reverse('management:sacco-settings')

    def _patch(self, payload):
        return self.client.patch(self.url, payload, format='json')

    def test_min_greater_than_max_rejected(self):
        response = self._patch(
            {'min_loan_amount': '60000.00', 'max_loan_amount': '50000.00'},
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.settings.refresh_from_db()
        self.assertEqual(self.settings.min_loan_amount, Decimal('1000.00'))

    def test_patch_only_max_below_stored_min_rejected(self):
        """A PATCH touching only max_loan_amount must validate against the
        currently-stored min_loan_amount, not treat it as absent."""
        response = self._patch({'max_loan_amount': '500.00'})

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.settings.refresh_from_db()
        self.assertEqual(self.settings.max_loan_amount, Decimal('50000.00'))

    def test_patch_only_min_above_stored_max_rejected(self):
        response = self._patch({'min_loan_amount': '90000.00'})

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.settings.refresh_from_db()
        self.assertEqual(self.settings.min_loan_amount, Decimal('1000.00'))

    def test_negative_registration_fee_rejected(self):
        response = self._patch({'registration_fee': '-1.00'})

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.settings.refresh_from_db()
        self.assertEqual(self.settings.registration_fee, Decimal('500.00'))

    def test_zero_loan_multiplier_rejected(self):
        response = self._patch({'loan_multiplier': 0})

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.settings.refresh_from_db()
        self.assertEqual(self.settings.loan_multiplier, 3)

    def test_valid_patch_still_succeeds(self):
        response = self._patch({'loan_multiplier': 4})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.settings.refresh_from_db()
        self.assertEqual(self.settings.loan_multiplier, 4)
