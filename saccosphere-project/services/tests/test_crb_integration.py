"""Tests for CRB (Credit Reference Bureau) integration."""

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import KYCVerification, Sacco, User
from saccomembership.models import Membership
from saccomanagement.models import Role
from services.integrations.metropol_client import MetropolClient, CRBCheckError
from services.models import CRBCheck, Loan, LoanType, Saving, SavingsType


@override_settings(
    METROPOL_API_KEY='test-key',
    METROPOL_API_URL='https://test.example.com',
    METROPOL_MOCK=True,
)
class MetropolClientTests(TestCase):
    """Test Metropol CRB client functionality."""

    def setUp(self):
        """Set up test data."""
        self.client = MetropolClient()

    def test_mock_response_deterministic(self):
        """Test that mock response is deterministic for same ID."""
        id_number = '12345678'
        
        response1 = self.client.check_credit(id_number)
        response2 = self.client.check_credit(id_number)
        
        # Same ID should return same score, band, and listed_negative
        self.assertEqual(response1['score'], response2['score'])
        self.assertEqual(response1['band'], response2['band'])
        self.assertEqual(response1['listed_negative'], response2['listed_negative'])
        self.assertEqual(response1['reference'], response2['reference'])

    def test_mock_response_different_ids(self):
        """Test that different IDs can return different results."""
        id_number1 = '12345678'
        id_number2 = '87654321'
        
        response1 = self.client.check_credit(id_number1)
        response2 = self.client.check_credit(id_number2)
        
        # Different IDs should have different references
        self.assertNotEqual(response1['reference'], response2['reference'])

    def test_mock_response_score_range(self):
        """Test that mock score is within valid range (300-850)."""
        id_number = '12345678'
        response = self.client.check_credit(id_number)
        
        self.assertGreaterEqual(response['score'], 300)
        self.assertLessEqual(response['score'], 850)

    def test_mock_response_band_derivation(self):
        """Test that band is correctly derived from score."""
        # Test various ID numbers to get different score ranges
        test_ids = ['12345678', '23456789', '34567890', '45678901', '56789012']
        
        valid_bands = [
            MetropolClient.POOR,
            MetropolClient.FAIR,
            MetropolClient.GOOD,
            MetropolClient.VERY_GOOD,
            MetropolClient.EXCELLENT,
        ]
        
        for id_number in test_ids:
            response = self.client.check_credit(id_number)
            self.assertIn(response['band'], valid_bands)

    def test_mock_response_structure(self):
        """Test that mock response has correct structure."""
        id_number = '12345678'
        response = self.client.check_credit(id_number)
        
        self.assertTrue(response['checked'])
        self.assertIsInstance(response['score'], int)
        self.assertIn(response['band'], [
            MetropolClient.POOR,
            MetropolClient.FAIR,
            MetropolClient.GOOD,
            MetropolClient.VERY_GOOD,
            MetropolClient.EXCELLENT,
        ])
        self.assertIsInstance(response['listed_negative'], bool)
        self.assertEqual(response['provider'], 'metropol')
        self.assertIn('MOCK-CRB-', response['reference'])


@override_settings(
    METROPOL_API_KEY='test-key',
    METROPOL_API_URL='https://test.example.com',
    METROPOL_MOCK=True,
)
class CRBCheckViewTests(TestCase):
    """Test CRB check API endpoint."""

    def setUp(self):
        """Set up test data."""
        self.api_client = APIClient()
        
        # Create SACCO
        self.sacco = Sacco.objects.create(
            name='Test SACCO',
            registration_number='TEST001',
            email='test@sacco.co.ke',
            phone='+254700000000',
            sector=Sacco.Sector.OTHER,
            county='Nairobi',
        )
        
        # Create users
        self.admin_user = User.objects.create_user(
            email='admin@test.com',
            password='testpass123',
            first_name='Admin',
            last_name='User',
            phone_number='+254711111111',
        )
        
        self.member_user = User.objects.create_user(
            email='member@test.com',
            password='testpass123',
            first_name='Member',
            last_name='User',
            phone_number='+254722222222',
        )
        
        # Create admin role
        self.admin_role = Role.objects.create(
            user=self.admin_user,
            name=Role.SACCO_ADMIN,
            sacco=self.sacco,
        )
        
        # Create membership
        self.membership = Membership.objects.create(
            user=self.member_user,
            sacco=self.sacco,
            member_number='MEM001',
            status=Membership.Status.APPROVED,
        )
        
        # Create KYC verification
        self.kyc = KYCVerification.objects.create(
            user=self.member_user,
            id_number='12345678',
            iprs_verified=True,
        )
        
        # Create loan type
        self.loan_type = LoanType.objects.create(
            sacco=self.sacco,
            name='Personal Loan',
            interest_rate=Decimal('12.5'),
            min_amount=Decimal('1000'),
            max_amount=Decimal('100000'),
            max_term_months=24,
            requires_guarantors=False,
        )
        
        # Create loan
        self.loan = Loan.objects.create(
            membership=self.membership,
            loan_type=self.loan_type,
            amount=Decimal('10000'),
            term_months=12,
            interest_rate=Decimal('12.5'),
            status=Loan.Status.PENDING_APPROVAL,
        )

    def test_crb_check_requires_auth(self):
        """Test that CRB check requires authentication."""
        response = self.api_client.post(
            f'/api/v1/services/loans/{self.loan.id}/crb-check/',
        )
        self.assertEqual(response.status_code, 401)

    def test_crb_check_requires_admin(self):
        """Test that CRB check requires SACCO admin permission."""
        self.api_client.force_authenticate(user=self.member_user)
        
        response = self.api_client.post(
            f'/api/v1/services/loans/{self.loan.id}/crb-check/',
        )
        self.assertEqual(response.status_code, 403)

    def test_crb_check_creates_record(self):
        """Test that CRB check creates a CRBCheck record."""
        self.api_client.force_authenticate(user=self.admin_user)
        
        response = self.api_client.post(
            f'/api/v1/services/loans/{self.loan.id}/crb-check/',
        )
        
        self.assertEqual(response.status_code, 201)
        self.assertEqual(CRBCheck.objects.count(), 1)
        
        crb_check = CRBCheck.objects.first()
        self.assertEqual(crb_check.loan, self.loan)
        self.assertEqual(crb_check.checked_by, self.admin_user)
        self.assertIsNotNone(crb_check.score)
        self.assertIsNotNone(crb_check.band)

    def test_crb_check_caches_result(self):
        """Test that CRB check returns cached result within 30 days."""
        self.api_client.force_authenticate(user=self.admin_user)
        
        # First check
        response1 = self.api_client.post(
            f'/api/v1/services/loans/{self.loan.id}/crb-check/',
        )
        self.assertEqual(response1.status_code, 201)
        self.assertFalse(response1.data['cached'])
        
        # Second check without force_refresh
        response2 = self.api_client.post(
            f'/api/v1/services/loans/{self.loan.id}/crb-check/',
        )
        self.assertEqual(response2.status_code, 200)
        self.assertTrue(response2.data['cached'])
        self.assertEqual(response1.data['id'], response2.data['id'])

    def test_crb_check_force_refresh(self):
        """Test that force_refresh bypasses cache."""
        self.api_client.force_authenticate(user=self.admin_user)
        
        # First check
        response1 = self.api_client.post(
            f'/api/v1/services/loans/{self.loan.id}/crb-check/',
        )
        self.assertEqual(response1.status_code, 201)
        
        # Second check with force_refresh
        response2 = self.api_client.post(
            f'/api/v1/services/loans/{self.loan.id}/crb-check/?force_refresh=true',
        )
        self.assertEqual(response2.status_code, 201)
        self.assertFalse(response2.data['cached'])
        # Should be a different record
        self.assertNotEqual(response1.data['id'], response2.data['id'])

    def test_crb_check_requires_kyc(self):
        """Test that CRB check requires KYC verification with ID number."""
        # Delete KYC
        self.kyc.delete()
        
        self.api_client.force_authenticate(user=self.admin_user)
        
        response = self.api_client.post(
            f'/api/v1/services/loans/{self.loan.id}/crb-check/',
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('KYC verification', response.data['detail'])


class LoanApprovalCRBTests(TestCase):
    """Test loan approval with CRB check requirements."""

    def setUp(self):
        """Set up test data."""
        self.api_client = APIClient()
        
        # Create SACCO
        self.sacco = Sacco.objects.create(
            name='Test SACCO',
            registration_number='TEST001',
            email='test@sacco.co.ke',
            phone='+254700000000',
            sector=Sacco.Sector.OTHER,
            county='Nairobi',
        )
        
        # Create users
        self.admin_user = User.objects.create_user(
            email='admin@test.com',
            password='testpass123',
            first_name='Admin',
            last_name='User',
            phone_number='+254711111111',
        )
        
        self.member_user = User.objects.create_user(
            email='member@test.com',
            password='testpass123',
            first_name='Member',
            last_name='User',
            phone_number='+254722222222',
        )
        
        # Create admin role
        self.admin_role = Role.objects.create(
            user=self.admin_user,
            name=Role.SACCO_ADMIN,
            sacco=self.sacco,
        )
        
        # Create membership
        self.membership = Membership.objects.create(
            user=self.member_user,
            sacco=self.sacco,
            member_number='MEM001',
            status=Membership.Status.APPROVED,
        )
        
        # Create KYC verification
        self.kyc = KYCVerification.objects.create(
            user=self.member_user,
            id_number='12345678',
            iprs_verified=True,
        )
        
        # Create loan type
        self.loan_type = LoanType.objects.create(
            sacco=self.sacco,
            name='Personal Loan',
            interest_rate=Decimal('12.5'),
            min_amount=Decimal('1000'),
            max_amount=Decimal('100000'),
            max_term_months=24,
            requires_guarantors=False,
        )
        
        # Create loan in UNDER_REVIEW status
        self.loan = Loan.objects.create(
            membership=self.membership,
            loan_type=self.loan_type,
            amount=Decimal('10000'),
            term_months=12,
            interest_rate=Decimal('12.5'),
            status=Loan.Status.UNDER_REVIEW,
        )

    def test_approval_requires_crb_check(self):
        """Test that loan approval requires CRB check."""
        self.api_client.force_authenticate(user=self.admin_user)
        
        response = self.api_client.patch(
            f'/api/v1/management/loans/{self.loan.id}/status/',
            {'status': Loan.Status.APPROVED},
        )
        
        self.assertEqual(response.status_code, 400)
        self.assertIn('CRB check required', response.data['detail'])

    def test_approval_with_clean_crb(self):
        """Test that loan approval succeeds with clean CRB check."""
        # Create CRB check with no negative listing
        crb_check = CRBCheck.objects.create(
            loan=self.loan,
            score=700,
            band=CRBCheck.CreditBand.GOOD,
            listed_negative=False,
            provider='metropol',
            reference='REF123',
            checked_by=self.admin_user,
        )
        
        self.api_client.force_authenticate(user=self.admin_user)
        
        response = self.api_client.patch(
            f'/api/v1/management/loans/{self.loan.id}/status/',
            {'status': Loan.Status.APPROVED},
        )
        
        self.assertEqual(response.status_code, 200)
        self.loan.refresh_from_db()
        self.assertEqual(self.loan.status, Loan.Status.APPROVED)

    def test_approval_with_negative_crb_requires_override(self):
        """Test that negative CRB listing requires override_reason."""
        # Create CRB check with negative listing
        crb_check = CRBCheck.objects.create(
            loan=self.loan,
            score=400,
            band=CRBCheck.CreditBand.POOR,
            listed_negative=True,
            provider='metropol',
            reference='REF123',
            checked_by=self.admin_user,
        )
        
        self.api_client.force_authenticate(user=self.admin_user)
        
        # Try without override_reason
        response = self.api_client.patch(
            f'/api/v1/management/loans/{self.loan.id}/status/',
            {'status': Loan.Status.APPROVED},
        )
        
        self.assertEqual(response.status_code, 400)
        self.assertIn('override_reason', response.data['detail'])

    def test_approval_with_negative_crb_and_valid_override(self):
        """Test that valid override allows approval with negative CRB."""
        # Create CRB check with negative listing
        crb_check = CRBCheck.objects.create(
            loan=self.loan,
            score=400,
            band=CRBCheck.CreditBand.POOR,
            listed_negative=True,
            provider='metropol',
            reference='REF123',
            checked_by=self.admin_user,
        )
        
        self.api_client.force_authenticate(user=self.admin_user)
        
        response = self.api_client.patch(
            f'/api/v1/management/loans/{self.loan.id}/status/',
            {
                'status': Loan.Status.APPROVED,
                'override_reason': 'Member has explained the negative listing and provided documentation.',
            },
        )
        
        self.assertEqual(response.status_code, 200)
        self.loan.refresh_from_db()
        self.assertEqual(self.loan.status, Loan.Status.APPROVED)

    def test_override_reason_minimum_length(self):
        """Test that override_reason must be at least 10 characters."""
        # Create CRB check with negative listing
        crb_check = CRBCheck.objects.create(
            loan=self.loan,
            score=400,
            band=CRBCheck.CreditBand.POOR,
            listed_negative=True,
            provider='metropol',
            reference='REF123',
            checked_by=self.admin_user,
        )
        
        self.api_client.force_authenticate(user=self.admin_user)
        
        # Try with short override_reason
        response = self.api_client.patch(
            f'/api/v1/management/loans/{self.loan.id}/status/',
            {
                'status': Loan.Status.APPROVED,
                'override_reason': 'Too short',
            },
        )
        
        self.assertEqual(response.status_code, 400)


class LoanApprovalListCRBTests(TestCase):
    """Test CRB fields in loan approval list view."""

    def setUp(self):
        """Set up test data."""
        self.api_client = APIClient()
        
        # Create SACCO
        self.sacco = Sacco.objects.create(
            name='Test SACCO',
            registration_number='TEST001',
            email='test@sacco.co.ke',
            phone='+254700000000',
            sector=Sacco.Sector.OTHER,
            county='Nairobi',
        )
        
        # Create users
        self.admin_user = User.objects.create_user(
            email='admin@test.com',
            password='testpass123',
            first_name='Admin',
            last_name='User',
            phone_number='+254711111111',
        )
        
        self.member_user = User.objects.create_user(
            email='member@test.com',
            password='testpass123',
            first_name='Member',
            last_name='User',
            phone_number='+254722222222',
        )
        
        # Create admin role
        self.admin_role = Role.objects.create(
            user=self.admin_user,
            name=Role.SACCO_ADMIN,
            sacco=self.sacco,
        )
        
        # Create membership
        self.membership = Membership.objects.create(
            user=self.member_user,
            sacco=self.sacco,
            member_number='MEM001',
            status=Membership.Status.APPROVED,
        )
        
        # Create loan type
        self.loan_type = LoanType.objects.create(
            sacco=self.sacco,
            name='Personal Loan',
            interest_rate=Decimal('12.5'),
            min_amount=Decimal('1000'),
            max_amount=Decimal('100000'),
            max_term_months=24,
            requires_guarantors=False,
        )
        
        # Create loan
        self.loan = Loan.objects.create(
            membership=self.membership,
            loan_type=self.loan_type,
            amount=Decimal('10000'),
            term_months=12,
            interest_rate=Decimal('12.5'),
            status=Loan.Status.PENDING_APPROVAL,
        )

    def test_approval_list_includes_crb_fields(self):
        """Test that loan approval list includes CRB status fields."""
        # Create CRB check
        crb_check = CRBCheck.objects.create(
            loan=self.loan,
            score=700,
            band=CRBCheck.CreditBand.GOOD,
            listed_negative=False,
            provider='metropol',
            reference='REF123',
            checked_by=self.admin_user,
        )
        
        self.api_client.force_authenticate(user=self.admin_user)
        
        response = self.api_client.get(
            '/api/v1/management/loans/approvals/',
            HTTP_X_SACCO_ID=str(self.sacco.id),
        )
        
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['results']), 1)
        
        loan_data = response.data['results'][0]
        self.assertEqual(loan_data['crb_status'], CRBCheck.CreditBand.GOOD)
        self.assertEqual(loan_data['crb_score'], 700)
        self.assertIsNotNone(loan_data['crb_checked_at'])
        self.assertFalse(loan_data['crb_listed_negative'])

    def test_approval_list_without_crb_check(self):
        """Test that CRB fields are null when no CRB check exists."""
        self.api_client.force_authenticate(user=self.admin_user)
        
        response = self.api_client.get(
            '/api/v1/management/loans/approvals/',
            HTTP_X_SACCO_ID=str(self.sacco.id),
        )
        
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['results']), 1)
        
        loan_data = response.data['results'][0]
        self.assertIsNone(loan_data['crb_status'])
        self.assertIsNone(loan_data['crb_score'])
        self.assertIsNone(loan_data['crb_checked_at'])
        self.assertIsNone(loan_data['crb_listed_negative'])
