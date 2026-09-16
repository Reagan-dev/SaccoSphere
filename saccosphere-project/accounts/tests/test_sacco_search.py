from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from accounts.models import Sacco, User
from saccomembership.models import Membership


class SaccoSearchTestCase(TestCase):
    """Test SACCO search and filtering functionality."""

    def setUp(self):
        """Set up test data."""
        self.client = APIClient()

        # Create test user
        self.user = User.objects.create_user(
            email='test@example.com',
            password='testpass123',
            first_name='Test',
            last_name='User',
        )

        # Create test SACCOs
        self.sacco_education_nairobi = Sacco.objects.create(
            name='Education SACCO Nairobi',
            sector=Sacco.Sector.EDUCATION,
            county='Nairobi',
            membership_type=Sacco.MembershipType.OPEN,
            is_publicly_listed=True,
            is_verified=True,
            registration_fee=Decimal('500.00'),
        )

        self.sacco_healthcare_mombasa = Sacco.objects.create(
            name='Healthcare Workers SACCO',
            sector=Sacco.Sector.HEALTHCARE,
            county='Mombasa',
            membership_type=Sacco.MembershipType.CLOSED,
            is_publicly_listed=True,
            is_verified=False,
            registration_fee=Decimal('1000.00'),
        )

        self.sacco_agriculture_kisumu = Sacco.objects.create(
            name='Farmers SACCO Kisumu',
            sector=Sacco.Sector.AGRICULTURE,
            county='Kisumu',
            membership_type=Sacco.MembershipType.OPEN,
            is_publicly_listed=True,
            is_verified=True,
            registration_fee=Decimal('250.00'),
        )

        # Create memberships to test member_count filtering
        for _ in range(5):
            user = User.objects.create_user(
                email=f'member{_}@example.com',
                password='testpass123',
                first_name=f'Member{_}',
                last_name='User',
            )
            Membership.objects.create(
                user=user,
                sacco=self.sacco_education_nairobi,
                status='APPROVED',
            )

        for _ in range(3):
            user = User.objects.create_user(
                email=f'agro{_}@example.com',
                password='testpass123',
                first_name=f'Agro{_}',
                last_name='User',
            )
            Membership.objects.create(
                user=user,
                sacco=self.sacco_agriculture_kisumu,
                status='APPROVED',
            )

    def test_sacco_search_by_name(self):
        """Test searching SACCOs by name."""
        url = reverse('accounts:sacco-list')
        response = self.client.get(url, {'search': 'Education'})

        self.assertEqual(response.status_code, 200)
        results = response.data['data']['results']
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['name'], 'Education SACCO Nairobi')

    def test_sacco_search_by_description(self):
        """Test searching SACCOs by description."""
        sacco = Sacco.objects.create(
            name='Tech Startup Fund',
            sector=Sacco.Sector.TECHNOLOGY,
            county='Nairobi',
            membership_type=Sacco.MembershipType.OPEN,
            is_publicly_listed=True,
            description='For software developers and tech professionals',
            registration_fee=Decimal('0.00'),
        )

        url = reverse('accounts:sacco-list')
        response = self.client.get(url, {'search': 'developers'})

        self.assertEqual(response.status_code, 200)
        results = response.data['data']['results']
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['name'], 'Tech Startup Fund')

    def test_sacco_filter_by_sector(self):
        """Test filtering SACCOs by sector."""
        url = reverse('accounts:sacco-list')
        response = self.client.get(url, {'sector': Sacco.Sector.EDUCATION})

        self.assertEqual(response.status_code, 200)
        results = response.data['data']['results']
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['sector'], 'EDUCATION')

    def test_sacco_filter_by_county(self):
        """Test filtering SACCOs by county (icontains)."""
        url = reverse('accounts:sacco-list')
        response = self.client.get(url, {'county': 'momb'})

        self.assertEqual(response.status_code, 200)
        results = response.data['data']['results']
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['county'], 'Mombasa')

    def test_sacco_filter_by_membership_type(self):
        """Test filtering SACCOs by membership type."""
        url = reverse('accounts:sacco-list')
        response = self.client.get(
            url,
            {'membership_type': Sacco.MembershipType.OPEN},
        )

        self.assertEqual(response.status_code, 200)
        results = response.data['data']['results']
        self.assertEqual(len(results), 2)  # Two OPEN SACCOs
        for result in results:
            self.assertEqual(result['membership_type'], 'OPEN')

    def test_sacco_filter_verified_only(self):
        """Test filtering verified SACCOs only."""
        url = reverse('accounts:sacco-list')
        response = self.client.get(url, {'verified_only': 'true'})

        self.assertEqual(response.status_code, 200)
        results = response.data['data']['results']
        self.assertEqual(len(results), 2)  # Two verified SACCOs
        for result in results:
            self.assertTrue(result['is_verified'])

    def test_sacco_filter_by_min_members(self):
        """Test filtering SACCOs by minimum member count."""
        url = reverse('accounts:sacco-list')
        response = self.client.get(url, {'min_members': 5})

        self.assertEqual(response.status_code, 200)
        results = response.data['data']['results']
        # Only Education SACCO has 5 members
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['member_count'], 5)

    def test_sacco_filter_by_max_members(self):
        """Test filtering SACCOs by maximum member count."""
        url = reverse('accounts:sacco-list')
        response = self.client.get(url, {'max_members': 3})

        self.assertEqual(response.status_code, 200)
        results = response.data['data']['results']
        # Agriculture SACCO has 3 members and Healthcare SACCO has 0 members;
        # Education SACCO (5 members) is excluded.
        self.assertEqual(len(results), 2)
        member_counts = {result['member_count'] for result in results}
        self.assertEqual(member_counts, {3, 0})

    def test_sacco_order_by_name(self):
        """Test ordering SACCOs by name."""
        url = reverse('accounts:sacco-list')
        response = self.client.get(url, {'ordering': 'name'})

        self.assertEqual(response.status_code, 200)
        results = response.data['data']['results']
        names = [r['name'] for r in results]
        self.assertEqual(names, sorted(names))

    def test_sacco_order_by_member_count(self):
        """Test ordering SACCOs by member count."""
        url = reverse('accounts:sacco-list')
        response = self.client.get(url, {'ordering': '-member_count'})

        self.assertEqual(response.status_code, 200)
        results = response.data['data']['results']
        self.assertEqual(results[0]['member_count'], 5)
        self.assertEqual(results[1]['member_count'], 3)

    def test_sacco_combined_filters(self):
        """Test combining multiple filters."""
        url = reverse('accounts:sacco-list')
        response = self.client.get(
            url,
            {
                'sector': Sacco.Sector.EDUCATION,
                'verified_only': 'true',
                'min_members': 1,
            },
        )

        self.assertEqual(response.status_code, 200)
        results = response.data['data']['results']
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['sector'], 'EDUCATION')
        self.assertTrue(results[0]['is_verified'])
        self.assertGreaterEqual(results[0]['member_count'], 1)

    def test_sacco_serializer_fields(self):
        """Test that serializer includes all required fields."""
        url = reverse('accounts:sacco-list')
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        results = response.data['data']['results']
        if results:
            result = results[0]
            # Check for required fields
            self.assertIn('id', result)
            self.assertIn('name', result)
            self.assertIn('member_count', result)
            self.assertIn('registration_fee', result)
            self.assertIn('membership_open', result)
            self.assertIn('can_apply', result)

    def test_sacco_registration_fee_display(self):
        """Test that registration_fee is displayed correctly."""
        url = reverse('accounts:sacco-list')
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        results = response.data['data']['results']

        # Find the Education SACCO
        for result in results:
            if result['name'] == 'Education SACCO Nairobi':
                self.assertEqual(result['registration_fee'], '500.00')
                break
        else:
            self.fail('Education SACCO not found in results')
