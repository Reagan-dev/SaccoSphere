"""Test savings categorisation and breakdown functionality."""

from decimal import Decimal
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework import status

from accounts.models import Sacco, User
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
