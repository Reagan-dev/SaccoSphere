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


class GuarantorWorkflowTestCase(TestCase):
    """Test guarantor approval and decline workflow."""

    def setUp(self):
        """Set up loan applicant, multiple guarantors, SACCO, and loan."""
        self.client = APIClient()

        # Create applicant user
        self.applicant = User.objects.create_user(
            email='applicant@example.com',
            first_name='Loan',
            last_name='Applicant',
            phone_number='254711111111',
            password='testpass123',
        )

        # Create two guarantor users
        self.guarantor_1 = User.objects.create_user(
            email='guarantor1@example.com',
            first_name='First',
            last_name='Guarantor',
            phone_number='254722222222',
            password='testpass123',
        )
        self.guarantor_2 = User.objects.create_user(
            email='guarantor2@example.com',
            first_name='Second',
            last_name='Guarantor',
            phone_number='254733333333',
            password='testpass123',
        )

        # Create SACCO
        self.sacco = Sacco.objects.create(
            name='Workflow SACCO',
            registration_number='WS001',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
            membership_type=Sacco.MembershipType.OPEN,
        )

        # Create memberships
        self.applicant_membership = Membership.objects.create(
            user=self.applicant,
            sacco=self.sacco,
            status=Membership.Status.APPROVED,
            member_number='APP001',
            approved_date=timezone.now(),
        )
        self.guarantor_1_membership = Membership.objects.create(
            user=self.guarantor_1,
            sacco=self.sacco,
            status=Membership.Status.APPROVED,
            member_number='GUA001',
            approved_date=timezone.now(),
        )
        self.guarantor_2_membership = Membership.objects.create(
            user=self.guarantor_2,
            sacco=self.sacco,
            status=Membership.Status.APPROVED,
            member_number='GUA002',
            approved_date=timezone.now(),
        )

        # Create savings for both guarantors
        self.savings_type = SavingsType.objects.create(
            sacco=self.sacco,
            name=SavingsType.Name.BOSA,
            minimum_contribution=Decimal('100.00'),
        )
        Saving.objects.create(
            membership=self.guarantor_1_membership,
            savings_type=self.savings_type,
            amount=Decimal('50000.00'),
            status=Saving.Status.ACTIVE,
        )
        Saving.objects.create(
            membership=self.guarantor_2_membership,
            savings_type=self.savings_type,
            amount=Decimal('40000.00'),
            status=Saving.Status.ACTIVE,
        )

        # Create loan type requiring guarantors
        self.loan_type = LoanType.objects.create(
            sacco=self.sacco,
            name='Guaranteed Loan',
            interest_rate=Decimal('12.00'),
            max_term_months=36,
            min_amount=Decimal('1000.00'),
            requires_guarantors=True,
            min_guarantors=2,
        )

        # Create loan in PENDING state
        self.loan = Loan.objects.create(
            membership=self.applicant_membership,
            loan_type=self.loan_type,
            amount=Decimal('30000.00'),
            interest_rate=Decimal('12.00'),
            term_months=12,
            outstanding_balance=Decimal('30000.00'),
            status=Loan.Status.PENDING,
        )

        # Create pending guarantor records
        self.guarantor_req_1 = Guarantor.objects.create(
            loan=self.loan,
            guarantor=self.guarantor_1,
            guarantee_amount=Decimal('15000.00'),
            status=Guarantor.Status.PENDING,
        )
        self.guarantor_req_2 = Guarantor.objects.create(
            loan=self.loan,
            guarantor=self.guarantor_2,
            guarantee_amount=Decimal('15000.00'),
            status=Guarantor.Status.PENDING,
        )

        # Transition loan to GUARANTORS_PENDING
        self.loan.status = Loan.Status.GUARANTORS_PENDING
        self.loan.save()

        # Initialize GuaranteeCapacity for both guarantors
        from services.models import GuaranteeCapacity

        GuaranteeCapacity.objects.create(
            user=self.guarantor_1,
            total_savings=Decimal('50000.00'),
            active_guarantees=Decimal('0.00'),
            available_capacity=Decimal('50000.00'),
        )
        GuaranteeCapacity.objects.create(
            user=self.guarantor_2,
            total_savings=Decimal('40000.00'),
            active_guarantees=Decimal('0.00'),
            available_capacity=Decimal('40000.00'),
        )

    def test_approve_all_guarantors_moves_to_board_review(self):
        """
        Test that approving all required guarantors moves loan to BOARD_REVIEW.

        When the minimum number of guarantors (loan_type.min_guarantors) approve,
        the loan should transition from GUARANTORS_PENDING to BOARD_REVIEW.
        """
        self.client.force_authenticate(user=self.guarantor_1)
        url = reverse(
            'services:guarantor-respond',
            kwargs={
                'loan_id': self.loan.id,
                'guarantor_id': self.guarantor_req_1.id,
            },
        )

        # First guarantor approves
        response = self.client.post(
            url,
            {'action': 'APPROVE', 'notes': 'I approve this loan.'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.guarantor_req_1.refresh_from_db()
        self.assertEqual(self.guarantor_req_1.status, Guarantor.Status.APPROVED)
        self.assertIsNotNone(self.guarantor_req_1.responded_at)

        # Loan should still be GUARANTORS_PENDING (only 1 of 2 approved)
        self.loan.refresh_from_db()
        self.assertEqual(self.loan.status, Loan.Status.GUARANTORS_PENDING)

        # Second guarantor approves
        self.client.force_authenticate(user=self.guarantor_2)
        url = reverse(
            'services:guarantor-respond',
            kwargs={
                'loan_id': self.loan.id,
                'guarantor_id': self.guarantor_req_2.id,
            },
        )

        response = self.client.post(
            url,
            {'action': 'APPROVE'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.guarantor_req_2.refresh_from_db()
        self.assertEqual(self.guarantor_req_2.status, Guarantor.Status.APPROVED)

        # Now loan should be BOARD_REVIEW (all 2 required approved)
        self.loan.refresh_from_db()
        self.assertEqual(self.loan.status, Loan.Status.BOARD_REVIEW)

    def test_decline_resets_loan_to_pending(self):
        """
        Test that declining a guarantee resets loan status to PENDING.

        When any guarantor declines after approval is requested, the loan
        should return to PENDING so the applicant can request other guarantors.
        """
        # Approve first guarantor
        self.client.force_authenticate(user=self.guarantor_1)
        url = reverse(
            'services:guarantor-respond',
            kwargs={
                'loan_id': self.loan.id,
                'guarantor_id': self.guarantor_req_1.id,
            },
        )

        response = self.client.post(
            url,
            {'action': 'APPROVE'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Second guarantor declines
        self.client.force_authenticate(user=self.guarantor_2)
        url = reverse(
            'services:guarantor-respond',
            kwargs={
                'loan_id': self.loan.id,
                'guarantor_id': self.guarantor_req_2.id,
            },
        )

        response = self.client.post(
            url,
            {'action': 'DECLINE', 'notes': 'Cannot commit to this guarantee.'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.guarantor_req_2.refresh_from_db()
        self.assertEqual(self.guarantor_req_2.status, Guarantor.Status.DECLINED)
        self.assertIsNotNone(self.guarantor_req_2.responded_at)

        # Loan should be back to PENDING
        self.loan.refresh_from_db()
        self.assertEqual(self.loan.status, Loan.Status.PENDING)

    def test_non_guarantor_cannot_respond(self):
        """
        Test that a user who is not the guarantor gets 403 Forbidden.

        Only the guarantor user can approve or decline their own guarantee.
        Other users should receive PermissionDenied (403).
        """
        # Try to respond as applicant (not the guarantor)
        self.client.force_authenticate(user=self.applicant)
        url = reverse(
            'services:guarantor-respond',
            kwargs={
                'loan_id': self.loan.id,
                'guarantor_id': self.guarantor_req_1.id,
            },
        )

        response = self.client.post(
            url,
            {'action': 'APPROVE'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_guarantor_cannot_approve_without_capacity(self):
        """
        Test that guarantor approval is blocked if capacity is insufficient.

        GuarantorCapacityCheck permission should prevent approval when the
        guarantor's available_capacity is less than guarantee_amount.
        """
        from services.models import GuaranteeCapacity

        # Manually reduce guarantor_1's available capacity
        capacity = GuaranteeCapacity.objects.get_or_create(
            user=self.guarantor_1,
        )[0]
        capacity.available_capacity = Decimal('5000.00')  # Less than 15000
        capacity.save()

        self.client.force_authenticate(user=self.guarantor_1)
        url = reverse(
            'services:guarantor-respond',
            kwargs={
                'loan_id': self.loan.id,
                'guarantor_id': self.guarantor_req_1.id,
            },
        )

        response = self.client.post(
            url,
            {'action': 'APPROVE'},
            format='json',
        )

        # Should return 400 (capacity check happens in view, not permission)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('Insufficient', response.data['detail'])


# ============================================================
# REVIEW — Guarantor workflow tests documentation
# ============================================================
#
# GuarantorWorkflowTestCase tests the complete guarantor approval/decline
# workflow in the context of loan applications that require guarantors.
#
# Key test scenarios:
# 1. test_approve_all_guarantors_moves_to_board_review:
#    - Creates loan with min_guarantors=2 and two PENDING guarantor requests.
#    - Approves first guarantor: loan stays GUARANTORS_PENDING (1 of 2).
#    - Approves second guarantor: loan transitions to BOARD_REVIEW (all approved).
#    - Validates: Guarantor.status, responded_at timestamps, Loan.status.
#
# 2. test_decline_resets_loan_to_pending:
#    - Approves first guarantor, then has second decline.
#    - Expects loan to revert to PENDING (not GUARANTORS_PENDING).
#    - Validates: Guarantor.status=DECLINED, responded_at set, Loan.status.
#
# 3. test_non_guarantor_cannot_respond:
#    - Tries to respond as applicant (different user).
#    - Expects 403 PermissionDenied response.
#    - Validates Django permission framework blocks unauthorized access.
#
# 4. test_guarantor_cannot_approve_without_capacity:
#    - Manually sets guarantor's available_capacity too low.
#    - Tries to approve guarantee_amount=15000 with capacity=5000.
#    - Expects 400 BadRequest (checked in view's atomic block).
#    - Validates: GuaranteeCapacity logic prevents over-commitment.
#
# Django/Python concepts used:
#    - TestCase: Django's test class for DB transactions/rollback.
#    - APIClient.force_authenticate(user=X): Login as user X without password.
#    - reverse(url_name, kwargs=...): Get URL from url pattern name.
#    - self.assertXXX: TestCase assertions for state validation.
#    - setUp(): Create fixtures before each test (clean state).
#    - refresh_from_db(): Reload model instance from DB (see DB changes).
#
# Manual test steps (using curl or Postman):
# 1. Create users: Alice (applicant), Bob, Carol (guarantors).
# 2. POST /api/v1/services/loans/apply/ as Alice → Loan.status=GUARANTORS_PENDING.
# 3. POST /api/v1/services/loans/{loan_id}/guarantors/ as Alice → creates 2 Guarantor records.
# 4. POST /api/v1/services/loans/{loan_id}/guarantors/{bob_id}/respond/ as Bob
#    with {\"action\": \"APPROVE\"} → Loan still GUARANTORS_PENDING.
# 5. POST /api/v1/services/loans/{loan_id}/guarantors/{carol_id}/respond/ as Carol
#    with {\"action\": \"APPROVE\"} → Loan transitions to BOARD_REVIEW.
# 6. Check notifications in DB: SACCO_ADMIN should have \"Loan Ready for Board Review\".
#
# Design decisions:
# - Each test is isolated via setUp() → guarantees clean state.
# - We use force_authenticate() instead of actual login for speed.
# - Loan status transitions are tested alongside DB record changes.
# - Capacity checks are tested in separate test (not combined with approval).
# - test_non_guarantor_cannot_respond confirms view-level permission checks.
#
# END OF REVIEW - DELETE EVERYTHING FROM THE FIRST # LINE ABOVE
