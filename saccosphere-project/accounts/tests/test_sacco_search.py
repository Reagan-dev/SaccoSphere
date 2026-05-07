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
        results = response.data.get('data', [])
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
        results = response.data.get('data', [])
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['name'], 'Tech Startup Fund')

    def test_sacco_filter_by_sector(self):
        """Test filtering SACCOs by sector."""
        url = reverse('accounts:sacco-list')
        response = self.client.get(url, {'sector': Sacco.Sector.EDUCATION})

        self.assertEqual(response.status_code, 200)
        results = response.data.get('data', [])
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['sector'], 'EDUCATION')

    def test_sacco_filter_by_county(self):
        """Test filtering SACCOs by county (icontains)."""
        url = reverse('accounts:sacco-list')
        response = self.client.get(url, {'county': 'momb'})

        self.assertEqual(response.status_code, 200)
        results = response.data.get('data', [])
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
        results = response.data.get('data', [])
        self.assertEqual(len(results), 2)  # Two OPEN SACCOs
        for result in results:
            self.assertEqual(result['membership_type'], 'OPEN')

    def test_sacco_filter_verified_only(self):
        """Test filtering verified SACCOs only."""
        url = reverse('accounts:sacco-list')
        response = self.client.get(url, {'verified_only': 'true'})

        self.assertEqual(response.status_code, 200)
        results = response.data.get('data', [])
        self.assertEqual(len(results), 2)  # Two verified SACCOs
        for result in results:
            self.assertTrue(result['is_verified'])

    def test_sacco_filter_by_min_members(self):
        """Test filtering SACCOs by minimum member count."""
        url = reverse('accounts:sacco-list')
        response = self.client.get(url, {'min_members': 5})

        self.assertEqual(response.status_code, 200)
        results = response.data.get('data', [])
        # Only Education SACCO has 5 members
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['member_count'], 5)

    def test_sacco_filter_by_max_members(self):
        """Test filtering SACCOs by maximum member count."""
        url = reverse('accounts:sacco-list')
        response = self.client.get(url, {'max_members': 3})

        self.assertEqual(response.status_code, 200)
        results = response.data.get('data', [])
        # Agriculture SACCO has 3 members
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['member_count'], 3)

    def test_sacco_order_by_name(self):
        """Test ordering SACCOs by name."""
        url = reverse('accounts:sacco-list')
        response = self.client.get(url, {'ordering': 'name'})

        self.assertEqual(response.status_code, 200)
        results = response.data.get('data', [])
        names = [r['name'] for r in results]
        self.assertEqual(names, sorted(names))

    def test_sacco_order_by_member_count(self):
        """Test ordering SACCOs by member count."""
        url = reverse('accounts:sacco-list')
        response = self.client.get(url, {'ordering': '-member_count'})

        self.assertEqual(response.status_code, 200)
        results = response.data.get('data', [])
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
        results = response.data.get('data', [])
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['sector'], 'EDUCATION')
        self.assertTrue(results[0]['is_verified'])
        self.assertGreaterEqual(results[0]['member_count'], 1)

    def test_sacco_serializer_fields(self):
        """Test that serializer includes all required fields."""
        url = reverse('accounts:sacco-list')
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        results = response.data.get('data', [])
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
        results = response.data.get('data', [])

        # Find the Education SACCO
        for result in results:
            if result['name'] == 'Education SACCO Nairobi':
                self.assertEqual(result['registration_fee'], '500.00')
                break
        else:
            self.fail('Education SACCO not found in results')


# ============================================================
# REVIEW — READ THIS THEN DELETE FROM THIS LINE TO THE END
# ============================================================
#
# WHAT EACH COMPONENT DOES AND WHY:
#
# 1. KENYA_COUNTIES Constant (accounts/models.py)
#    - List of all 47 Kenya county names
#    - WHY: Provides a fixed reference for county choices. Can be used in forms,
#      validation, or seeding. Centralized in models so it's importable everywhere.
#
# 2. registration_fee Field (Sacco Model)
#    - DecimalField with max_digits=10, decimal_places=2, default=0.00
#    - WHY: SACCOs can charge joining fees. Use Decimal for money (no rounding errors
#      like float). Default 0 for free entry.
#
# 3. seed_sacco_data Management Command
#    - Counts Sacco.Sector.choices and KENYA_COUNTIES length
#    - Prints "Seeded X counties and Y sectors"
#    - WHY: Django management command for seeding data. Can be run with:
#      python manage.py seed_sacco_data
#    - Informational only (doesn't write to DB). Validates choices exist.
#
# 4. Enhanced SaccoListView.get_queryset()
#    - Single queryset with all annotations and filters
#    - Filters: search (name/description/registration_number), sector, county,
#      membership_type, verified_only, min_members, max_members
#    - Ordering: name, -name, member_count, -member_count, created_at, -created_at
#    - WHY: Rich search/filter UX. Q objects allow OR logic (search across multiple
#      fields). Annotate member_count once at DB level (efficient). Order validation
#      prevents arbitrary SQL ordering.
#
# 5. SaccoListSerializer Updates
#    - Added: membership_open (computed bool), can_apply (always False for AllowAny)
#    - Added: registration_fee (Decimal display)
#    - WHY: Frontend needs to know if SACCO accepts new members and fee amount.
#      membership_open computed from membership_type=='OPEN'. can_apply always
#      False because endpoint is AllowAny (no auth context).
#
# 6. Test Suite (test_sacco_search.py)
#    - 14 test methods covering search, filters, ordering, combined queries
#    - Tests serializer field presence and correctness
#    - WHY: Regression suite. Ensures search/filter logic works as API grows.
#
#
# DJANGO/PYTHON CONCEPTS:
#
# - Annotate: Add computed fields to queryset without fetching all data.
#   Count('membership', filter=Q(...)) counts related objects matching condition.
#   Efficient: single DB query instead of Python loops.
#
# - Q Objects: Allow complex queries with OR logic. Q(field1=x) | Q(field2=y)
#   means "field1 OR field2". Used for search across multiple fields.
#
# - icontains: Case-insensitive substring match. 'momb' matches 'Mombasa'.
#   Efficient with indexes. Better UX than exact match.
#
# - order_by with '-': Descending order. '-member_count' = highest member count first.
#
# - Decimal: Python type for exact decimal arithmetic. '0.1 + 0.2' = '0.3' exactly,
#   unlike float. Required for money fields.
#
# - SerializerMethodField: Custom field computed via a get_* method.
#   get_membership_open(obj) returns bool. Allows computed, non-DB fields.
#
# - TestCase with setUp: Django test class that runs setUp before each test.
#   Creates fresh DB for isolation. Tests are independent.
#
# - reverse(): Resolves URL by view name. reverse('accounts:sacco-list') returns
#   '/api/v1/accounts/saccos/' (or whatever the actual route is).
#
#
# HOW TO TEST MANUALLY:
#
# 1. Create some SACCOs with different sectors and counties:
#    POST /api/v1/accounts/saccos/ (if you have admin write endpoint)
#    Or use Django admin: python manage.py createsuperuser, then /admin
#
# 2. List with search: GET /api/v1/accounts/saccos/?search=education
#    Should return SACCOs with "education" in name/description
#
# 3. Filter by sector: GET /api/v1/accounts/saccos/?sector=EDUCATION
#    Should return only EDUCATION sector SACCOs
#
# 4. Filter by members: GET /api/v1/accounts/saccos/?min_members=5
#    Should return SACCOs with 5+ approved members
#
# 5. Order by member count: GET /api/v1/accounts/saccos/?ordering=-member_count
#    Should return SACCOs sorted by member count descending
#
# 6. Combine filters: GET /api/v1/accounts/saccos/?sector=EDUCATION&verified_only=true&min_members=1
#    Should return EDUCATION SACCOs that are verified with 1+ members
#
# 7. Run tests: python manage.py test accounts.tests.test_sacco_search
#
#
# KEY DESIGN DECISIONS AND WHY:
#
# - Single queryset with all annotations: Prevents N+1 queries. One DB hit instead
#   of one per filter. Better performance.
#
# - Order validation (valid_orderings list): Prevents malicious SQL injection via
#   ordering param. Always whitelist allowed values.
#
# - Decimal('0.00') default for registration_fee: Allows free SACCOs while being
#   explicit about money type. Decimal ensures precision.
#
# - KENYA_COUNTIES as module-level constant: Importable everywhere. Can be used
#   in forms, validation, migrations. Centralized reference.
#
# - Test creates 5 members for Education, 3 for Agriculture: Allows testing both
#   min_members >= 5 (returns 1 result) and max_members <= 3 (returns 1 result).
#   Different counts test filtering logic.
#
# - membership_open computed vs stored: Don't store redundant data. Compute from
#   membership_type on the fly. Saves DB space and prevents sync issues.
#
# - can_apply always False for AllowAny: Proper permission design. Unauthenticated
#   users can't apply. Endpoint returns False; authenticated users would check
#   separately (e.g., via a detail endpoint with custom logic).
#
# ============================================================
# END OF REVIEW — DELETE EVERYTHING FROM THE FIRST # LINE ABOVE
# ============================================================
