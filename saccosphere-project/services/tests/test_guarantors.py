"""Test guarantor search and request endpoints."""

from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models import Sacco, User
from saccomembership.models import Membership
from services.models import (
    Guarantor,
    Loan,
    LoanType,
    Saving,
    SavingsType,
)


class GuarantorEndpointTestCase(TestCase):
    """Test guarantor search and request API behavior."""

    def setUp(self):
        """Set up loan applicant, guarantor, SACCO, savings, and loan."""
        self.client = APIClient()
        self.applicant = User.objects.create_user(
            email='applicant@example.com',
            first_name='Loan',
            last_name='Applicant',
            phone_number='254711111111',
            password='testpass123',
        )
        self.guarantor_user = User.objects.create_user(
            email='guarantor@example.com',
            first_name='Good',
            last_name='Guarantor',
            phone_number='254722222222',
            password='testpass123',
        )
        self.sacco = Sacco.objects.create(
            name='Guarantor SACCO',
            registration_number='GS001',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
            membership_type=Sacco.MembershipType.OPEN,
        )
        self.applicant_membership = Membership.objects.create(
            user=self.applicant,
            sacco=self.sacco,
            status=Membership.Status.APPROVED,
            member_number='APP001',
            approved_date=timezone.now(),
        )
        self.guarantor_membership = Membership.objects.create(
            user=self.guarantor_user,
            sacco=self.sacco,
            status=Membership.Status.APPROVED,
            member_number='GUA001',
            approved_date=timezone.now(),
        )
        self.savings_type = SavingsType.objects.create(
            sacco=self.sacco,
            name=SavingsType.Name.BOSA,
            minimum_contribution=Decimal('100.00'),
        )
        Saving.objects.create(
            membership=self.guarantor_membership,
            savings_type=self.savings_type,
            amount=Decimal('50000.00'),
            status=Saving.Status.ACTIVE,
        )
        self.loan_type = LoanType.objects.create(
            sacco=self.sacco,
            name='Development Loan',
            interest_rate=Decimal('12.00'),
            max_term_months=36,
            min_amount=Decimal('1000.00'),
        )
        self.loan = Loan.objects.create(
            membership=self.applicant_membership,
            loan_type=self.loan_type,
            amount=Decimal('30000.00'),
            interest_rate=Decimal('12.00'),
            term_months=12,
            outstanding_balance=Decimal('30000.00'),
            status=Loan.Status.PENDING,
        )

    def test_search_finds_by_phone(self):
        """Test that guarantor search finds an eligible member by phone."""
        self.client.force_authenticate(user=self.applicant)
        url = reverse(
            'services:guarantor-search',
            kwargs={'loan_id': self.loan.id},
        )

        response = self.client.get(url, {'phone': '722222222'})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data['user']['id'],
            str(self.guarantor_user.id),
        )
        self.assertEqual(response.data['member_number'], 'GUA001')
        self.assertEqual(response.data['savings_total'], '50000.00')
        self.assertTrue(response.data['can_guarantee'])

    def test_search_excludes_applicant(self):
        """Test that a loan applicant cannot be returned as guarantor."""
        self.client.force_authenticate(user=self.applicant)
        url = reverse(
            'services:guarantor-search',
            kwargs={'loan_id': self.loan.id},
        )

        response = self.client.get(url, {'phone': '711111111'})

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_request_creates_guarantor_record(self):
        """Test that requesting a guarantor creates a pending record."""
        self.client.force_authenticate(user=self.applicant)
        url = reverse(
            'services:guarantor-request',
            kwargs={'loan_id': self.loan.id},
        )

        response = self.client.post(
            url,
            {
                'guarantor_user_id': str(self.guarantor_user.id),
                'guarantee_amount': '10000.00',
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(
            Guarantor.objects.filter(
                loan=self.loan,
                guarantor=self.guarantor_user,
                status=Guarantor.Status.PENDING,
                guarantee_amount=Decimal('10000.00'),
            ).exists()
        )

        self.loan.refresh_from_db()
        self.assertEqual(self.loan.status, Loan.Status.GUARANTORS_PENDING)
