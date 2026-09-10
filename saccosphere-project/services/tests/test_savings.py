"""Test savings categorisation and breakdown functionality."""

from decimal import Decimal
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework import status

from accounts.models import Sacco, User
from saccomanagement.models import Role
from saccomembership.models import Membership
from services.models import Saving, SavingsType


class SavingsBreakdownTestCase(TestCase):
    """Test savings breakdown endpoint functionality."""

    def setUp(self):
        """Set up test data."""
        self.client = APIClient()
        
        # Create test user
        self.user = User.objects.create_user(
            email='test@example.com',
            first_name='Test',
            last_name='User',
            password='testpass123'
        )
        
        # Create test SACCO
        self.sacco = Sacco.objects.create(
            name='Test SACCO',
            registration_number='TS001',
            sector=Sacco.Sector.EDUCATION,
            county='Nairobi',
            membership_type=Sacco.MembershipType.OPEN,
            is_publicly_listed=True,
            is_verified=True,
        )
        
        # Create membership
        self.membership = Membership.objects.create(
            user=self.user,
            sacco=self.sacco,
            status=Membership.Status.APPROVED,
            member_number='M001',
        )
        
        # Create savings types
        self.bosa_type = SavingsType.objects.create(
            sacco=self.sacco,
            name=SavingsType.Name.BOSA,
            description='Basic Ordinary Savings Account',
            minimum_contribution=Decimal('100.00'),
        )
        
        self.fosa_type = SavingsType.objects.create(
            sacco=self.sacco,
            name=SavingsType.Name.FOSA,
            description='Fixed Deposit Savings Account',
            minimum_contribution=Decimal('500.00'),
        )
        
        self.share_type = SavingsType.objects.create(
            sacco=self.sacco,
            name=SavingsType.Name.SHARE_CAPITAL,
            description='Share Capital Account',
            minimum_contribution=Decimal('1000.00'),
        )

    def test_savings_breakdown_returns_correct_bosa_fosa_totals(self):
        """Test that breakdown endpoint returns correct BOSA/FOSA totals."""
        # Create savings with different types
        Saving.objects.create(
            membership=self.membership,
            savings_type=self.bosa_type,
            amount=Decimal('5000.00'),
            status=Saving.Status.ACTIVE,
            dividend_eligible=True,
        )
        
        Saving.objects.create(
            membership=self.membership,
            savings_type=self.fosa_type,
            amount=Decimal('3000.00'),
            status=Saving.Status.ACTIVE,
            dividend_eligible=False,
        )
        
        Saving.objects.create(
            membership=self.membership,
            savings_type=self.share_type,
            amount=Decimal('2000.00'),
            status=Saving.Status.ACTIVE,
            dividend_eligible=True,
        )
        
        # Authenticate and get breakdown
        self.client.force_authenticate(user=self.user)
        url = reverse('services:savings-breakdown')
        response = self.client.get(url, {'sacco_id': self.sacco.id})
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()['data']
        
        # Check individual totals
        self.assertEqual(data['bosa_total'], Decimal('5000.00'))
        self.assertEqual(data['fosa_total'], Decimal('3000.00'))
        self.assertEqual(data['share_capital_total'], Decimal('2000.00'))
        self.assertEqual(data['sacco_id'], str(self.sacco.id))
        self.assertEqual(data['sacco_name'], self.sacco.name)

    def test_savings_breakdown_total_equals_sum_of_all_active_savings(self):
        """Test that total equals sum of all active savings."""
        # Create multiple savings
        Saving.objects.create(
            membership=self.membership,
            savings_type=self.bosa_type,
            amount=Decimal('1500.00'),
            status=Saving.Status.ACTIVE,
            dividend_eligible=True,
        )
        
        Saving.objects.create(
            membership=self.membership,
            savings_type=self.fosa_type,
            amount=Decimal('2500.00'),
            status=Saving.Status.ACTIVE,
            dividend_eligible=True,
        )
        
        Saving.objects.create(
            membership=self.membership,
            savings_type=self.share_type,
            amount=Decimal('1000.00'),
            status=Saving.Status.ACTIVE,
            dividend_eligible=False,
        )
        
        # Authenticate and get breakdown
        self.client.force_authenticate(user=self.user)
        url = reverse('services:savings-breakdown')
        response = self.client.get(url, {'sacco_id': self.sacco.id})
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()['data']
        
        # Check total calculation
        expected_total = Decimal('1500.00') + Decimal('2500.00') + Decimal('1000.00')
        self.assertEqual(data['total'], expected_total)

    def test_savings_breakdown_dividend_eligible_total_excludes_non_eligible_records(self):
        """Test that dividend_eligible_total excludes non-eligible records."""
        # Create eligible and non-eligible savings
        Saving.objects.create(
            membership=self.membership,
            savings_type=self.bosa_type,
            amount=Decimal('3000.00'),
            status=Saving.Status.ACTIVE,
            dividend_eligible=True,
        )
        
        Saving.objects.create(
            membership=self.membership,
            savings_type=self.fosa_type,
            amount=Decimal('2000.00'),
            status=Saving.Status.ACTIVE,
            dividend_eligible=False,  # Not eligible for dividends
        )
        
        Saving.objects.create(
            membership=self.membership,
            savings_type=self.share_type,
            amount=Decimal('1500.00'),
            status=Saving.Status.ACTIVE,
            dividend_eligible=True,
        )
        
        # Authenticate and get breakdown
        self.client.force_authenticate(user=self.user)
        url = reverse('services:savings-breakdown')
        response = self.client.get(url, {'sacco_id': self.sacco.id})
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()['data']
        
        # Check dividend eligible calculation (should exclude non-eligible)
        expected_dividend_total = Decimal('3000.00') + Decimal('1500.00')  # Only eligible ones
        self.assertEqual(data['dividend_eligible_total'], expected_dividend_total)

    def test_savings_breakdown_requires_sacco_id_parameter(self):
        """Test that breakdown endpoint requires sacco_id parameter."""
        self.client.force_authenticate(user=self.user)
        url = reverse('services:savings-breakdown')
        
        # Test without sacco_id
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('sacco_id parameter is required', response.json()['message'])

    def test_savings_breakdown_returns_zero_for_no_savings(self):
        """Test that breakdown returns zeros when user has no savings."""
        # Don't create any savings
        
        # Authenticate and get breakdown
        self.client.force_authenticate(user=self.user)
        url = reverse('services:savings-breakdown')
        response = self.client.get(url, {'sacco_id': self.sacco.id})
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()['data']
        
        # Check all totals are zero
        self.assertEqual(data['bosa_total'], Decimal('0.00'))
        self.assertEqual(data['fosa_total'], Decimal('0.00'))
        self.assertEqual(data['share_capital_total'], Decimal('0.00'))
        self.assertEqual(data['total'], Decimal('0.00'))
        self.assertEqual(data['dividend_eligible_total'], Decimal('0.00'))

    def test_savings_breakdown_excludes_inactive_savings(self):
        """Test that breakdown excludes inactive savings."""
        # Create active and inactive savings
        Saving.objects.create(
            membership=self.membership,
            savings_type=self.bosa_type,
            amount=Decimal('2000.00'),
            status=Saving.Status.ACTIVE,  # Active - should be included
            dividend_eligible=True,
        )
        
        Saving.objects.create(
            membership=self.membership,
            savings_type=self.fosa_type,
            amount=Decimal('1500.00'),
            status=Saving.Status.FROZEN,  # Frozen - should be excluded
            dividend_eligible=True,
        )
        
        Saving.objects.create(
            membership=self.membership,
            savings_type=self.share_type,
            amount=Decimal('1000.00'),
            status=Saving.Status.CLOSED,  # Closed - should be excluded
            dividend_eligible=True,
        )
        
        # Authenticate and get breakdown
        self.client.force_authenticate(user=self.user)
        url = reverse('services:savings-breakdown')
        response = self.client.get(url, {'sacco_id': self.sacco.id})
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()['data']
        
        # Only active savings should be included
        self.assertEqual(data['bosa_total'], Decimal('2000.00'))  # Only active BOSA
        self.assertEqual(data['fosa_total'], Decimal('0.00'))  # FOSA is frozen
        self.assertEqual(data['share_capital_total'], Decimal('0.00'))  # Share is closed
        self.assertEqual(data['total'], Decimal('2000.00'))  # Only active total

    def test_savings_breakdown_requires_authentication(self):
        """Test that breakdown endpoint requires authentication."""
        url = reverse('services:savings-breakdown')

        # Test without authentication
        response = self.client.get(url, {'sacco_id': self.sacco.id})
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_savings_breakdown_is_scoped_to_the_requesting_member_only(self):
        """Another member's savings in the same SACCO must not leak in.

        Regression guard for SavingsBreakdownView's
        ``membership__user=request.user`` scoping: the audit lists this
        as already-correct, so pin it.
        """
        # The caller's own savings.
        Saving.objects.create(
            membership=self.membership,
            savings_type=self.bosa_type,
            amount=Decimal('2000.00'),
            status=Saving.Status.ACTIVE,
            dividend_eligible=True,
        )

        # A second APPROVED member of the SAME SACCO with a large
        # balance that must never be counted for self.user.
        other_user = User.objects.create_user(
            email='other-breakdown@example.com',
            password='testpass123',
        )
        other_membership = Membership.objects.create(
            user=other_user,
            sacco=self.sacco,
            status=Membership.Status.APPROVED,
            member_number='M002',
        )
        Saving.objects.create(
            membership=other_membership,
            savings_type=self.bosa_type,
            amount=Decimal('999999.00'),
            status=Saving.Status.ACTIVE,
            dividend_eligible=True,
        )

        self.client.force_authenticate(user=self.user)
        url = reverse('services:savings-breakdown')
        response = self.client.get(url, {'sacco_id': self.sacco.id})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()['data']
        self.assertEqual(data['bosa_total'], Decimal('2000.00'))
        self.assertEqual(data['total'], Decimal('2000.00'))


class SavingsTypeDeletionTests(TestCase):
    """Hard-deleting a savings type in use must be refused (409)."""

    def setUp(self):
        self.client = APIClient()
        self.sacco = Sacco.objects.create(
            name='Del SACCO',
            registration_number='DELT-1',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
        )
        self.admin = User.objects.create_user(
            email='del-admin@example.com',
            password='StrongPass1',
        )
        Role.objects.create(
            user=self.admin, sacco=self.sacco, name=Role.SACCO_ADMIN,
        )
        self.client.force_authenticate(user=self.admin)

        self.unused_type = SavingsType.objects.create(
            sacco=self.sacco,
            name=SavingsType.Name.FOSA,
            minimum_contribution=Decimal('500.00'),
        )
        self.used_type = SavingsType.objects.create(
            sacco=self.sacco,
            name=SavingsType.Name.BOSA,
            minimum_contribution=Decimal('100.00'),
        )
        member = User.objects.create_user(
            email='del-member@example.com',
            password='StrongPass1',
        )
        membership = Membership.objects.create(
            user=member,
            sacco=self.sacco,
            status=Membership.Status.APPROVED,
            member_number='DELT-M1',
        )
        Saving.objects.create(
            membership=membership,
            savings_type=self.used_type,
            amount=Decimal('4200.00'),
            status=Saving.Status.ACTIVE,
        )

    def _detail_url(self, savings_type):
        return reverse(
            'services:savings-type-detail', args=[savings_type.id],
        )

    def test_delete_unused_savings_type_succeeds(self):
        response = self.client.delete(
            self._detail_url(self.unused_type),
            HTTP_X_SACCO_ID=str(self.sacco.id),
        )
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(
            SavingsType.objects.filter(id=self.unused_type.id).exists()
        )

    def test_delete_savings_type_with_accounts_is_rejected(self):
        response = self.client.delete(
            self._detail_url(self.used_type),
            HTTP_X_SACCO_ID=str(self.sacco.id),
        )
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        detail = response.json()['detail']
        self.assertIn('1 savings account', detail)
        self.assertIn('is_active=false', detail)
        self.assertTrue(
            SavingsType.objects.filter(id=self.used_type.id).exists()
        )

    def test_retire_via_is_active_false_is_the_supported_path(self):
        response = self.client.patch(
            self._detail_url(self.used_type),
            {'is_active': False},
            format='json',
            HTTP_X_SACCO_ID=str(self.sacco.id),
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.used_type.refresh_from_db()
        self.assertFalse(self.used_type.is_active)


class MultipleAccountsPerTypeTests(TestCase):
    """SavingsType.allows_multiple_accounts gates the one-per-type rule."""

    def setUp(self):
        self.sacco = Sacco.objects.create(
            name='Multi SACCO',
            registration_number='MULT-1',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
        )
        user = User.objects.create_user(
            email='multi-member@example.com',
            password='StrongPass1',
        )
        self.membership = Membership.objects.create(
            user=user,
            sacco=self.sacco,
            status=Membership.Status.APPROVED,
            member_number='MULT-M1',
        )
        self.single_type = SavingsType.objects.create(
            sacco=self.sacco,
            name=SavingsType.Name.BOSA,
            minimum_contribution=Decimal('100.00'),
        )
        self.multi_type = SavingsType.objects.create(
            sacco=self.sacco,
            name=SavingsType.Name.FOSA,
            minimum_contribution=Decimal('500.00'),
            allows_multiple_accounts=True,
        )
        self.existing_single = Saving.objects.create(
            membership=self.membership,
            savings_type=self.single_type,
            amount=Decimal('0.00'),
        )
        Saving.objects.create(
            membership=self.membership,
            savings_type=self.multi_type,
            amount=Decimal('0.00'),
        )

    def test_default_type_still_rejects_a_second_account(self):
        duplicate = Saving(
            membership=self.membership,
            savings_type=self.single_type,
            amount=Decimal('0.00'),
        )
        with self.assertRaises(ValidationError):
            duplicate.full_clean()

    def test_flagged_type_permits_a_second_account(self):
        second = Saving(
            membership=self.membership,
            savings_type=self.multi_type,
            amount=Decimal('0.00'),
        )
        second.full_clean()  # must not raise
        second.save()
        self.assertEqual(
            Saving.objects.filter(
                membership=self.membership,
                savings_type=self.multi_type,
            ).count(),
            2,
        )

    def test_revalidating_an_existing_account_is_not_a_duplicate(self):
        # Editing the row in place must not trip the "already has one"
        # check against itself.
        self.existing_single.full_clean()


class SavingsTypeReadAccessTests(TestCase):
    """list/retrieve must not leak every SACCO's product configuration."""

    LIST_URL = '/api/v1/services/savings-types/'

    def setUp(self):
        self.client = APIClient()
        self.sacco_a = Sacco.objects.create(
            name='Read A',
            registration_number='STRD-A',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
        )
        self.sacco_b = Sacco.objects.create(
            name='Read B',
            registration_number='STRD-B',
            sector=Sacco.Sector.FINANCE,
            county='Kiambu',
        )
        self.type_a = SavingsType.objects.create(
            sacco=self.sacco_a,
            name=SavingsType.Name.BOSA,
            description='A BOSA product',
            interest_rate=Decimal('7.50'),
            minimum_contribution=Decimal('100.00'),
        )
        self.type_b = SavingsType.objects.create(
            sacco=self.sacco_b,
            name=SavingsType.Name.FOSA,
            interest_rate=Decimal('4.00'),
            minimum_contribution=Decimal('500.00'),
        )
        self.member = User.objects.create_user(
            email='rd-member@example.com', password='StrongPass1',
        )
        Membership.objects.create(
            user=self.member,
            sacco=self.sacco_a,
            status=Membership.Status.APPROVED,
            member_number='STRD-M1',
        )
        self.admin_a = User.objects.create_user(
            email='rd-admin-a@example.com', password='StrongPass1',
        )
        Role.objects.create(
            user=self.admin_a, sacco=self.sacco_a, name=Role.SACCO_ADMIN,
        )

    def _detail_url(self, savings_type):
        return reverse(
            'services:savings-type-detail', args=[savings_type.id],
        )

    def test_anonymous_cannot_list_savings_types(self):
        response = self.client.get(self.LIST_URL)
        self.assertIn(response.status_code, (401, 403))

        # ...and cannot get around it by naming a SACCO either.
        scoped = self.client.get(
            self.LIST_URL, {'sacco': str(self.sacco_a.id)},
        )
        self.assertIn(scoped.status_code, (401, 403))

    def test_anonymous_cannot_retrieve_a_savings_type(self):
        response = self.client.get(self._detail_url(self.type_a))
        self.assertIn(response.status_code, (401, 403))

    def test_authenticated_list_requires_a_sacco_param(self):
        self.client.force_authenticate(self.member)
        response = self.client.get(self.LIST_URL)
        self.assertEqual(response.status_code, 400)

    def test_list_is_scoped_to_the_requested_sacco_only(self):
        self.client.force_authenticate(self.member)
        response = self.client.get(
            self.LIST_URL, {'sacco': str(self.sacco_a.id)},
        )
        self.assertEqual(response.status_code, 200)

        rows = response.json()['data']['results']
        names = {row['name'] for row in rows}
        self.assertEqual(names, {SavingsType.Name.BOSA})  # only SACCO A

    def test_non_admin_list_omits_internal_fields(self):
        self.client.force_authenticate(self.member)
        response = self.client.get(
            self.LIST_URL, {'sacco': str(self.sacco_a.id)},
        )
        rows = response.json()['data']['results']
        row = rows[0]
        self.assertEqual(
            set(row),
            {'name', 'description', 'minimum_contribution', 'interest_rate'},
        )
        for leaked in ('id', 'sacco', 'sacco_id', 'is_active',
                       'allows_multiple_accounts'):
            self.assertNotIn(leaked, row)

    def test_non_admin_cannot_widen_another_saccos_config(self):
        # A member of SACCO A asking for SACCO B still only gets the
        # narrow public view, never is_active / ids.
        self.client.force_authenticate(self.member)
        response = self.client.get(
            self.LIST_URL, {'sacco': str(self.sacco_b.id)},
        )
        self.assertEqual(response.status_code, 200)
        rows = response.json()['data']['results']
        self.assertEqual({r['name'] for r in rows}, {SavingsType.Name.FOSA})
        self.assertNotIn('is_active', rows[0])
        self.assertNotIn('id', rows[0])

    def test_sacco_admin_list_includes_management_fields(self):
        self.client.force_authenticate(self.admin_a)
        response = self.client.get(
            self.LIST_URL,
            {'sacco': str(self.sacco_a.id)},
            HTTP_X_SACCO_ID=str(self.sacco_a.id),
        )
        self.assertEqual(response.status_code, 200)
        rows = response.json()['data']['results']
        self.assertIn('id', rows[0])
        self.assertIn('is_active', rows[0])

    def test_retrieve_uses_the_narrow_serializer(self):
        self.client.force_authenticate(self.member)
        response = self.client.get(self._detail_url(self.type_a))
        self.assertEqual(response.status_code, 200)
        body = response.json()
        row = body.get('data', body)
        self.assertNotIn('id', row)
        self.assertNotIn('is_active', row)
        self.assertEqual(row['name'], SavingsType.Name.BOSA)

    def test_sacco_admin_can_still_create_a_savings_type(self):
        self.client.force_authenticate(self.admin_a)
        response = self.client.post(
            self.LIST_URL,
            {
                'name': SavingsType.Name.SHARE_CAPITAL,
                'minimum_contribution': '1000.00',
                'interest_rate': '3.00',
            },
            format='json',
            HTTP_X_SACCO_ID=str(self.sacco_a.id),
        )
        self.assertEqual(response.status_code, 201)
        self.assertTrue(
            SavingsType.objects.filter(
                sacco=self.sacco_a, name=SavingsType.Name.SHARE_CAPITAL,
            ).exists()
        )
