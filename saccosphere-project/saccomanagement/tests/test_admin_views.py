"""Tests for SACCO admin dashboard API views."""

from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models import Sacco, User
from notifications.models import Notification
from saccomanagement.models import Role, SystemAuditLog
from saccomembership.models import Membership, SaccoApplication
from saccomembership.serializers import MembershipApplySerializer
from payments.models import MpesaTransaction, Transaction
from services.models import (
    Loan,
    RepaymentSchedule,
    Saving,
    SavingsType,
)


class SaccoAdminDashboardViewsTestCase(TestCase):
    """Test SACCO-scoped admin dashboard endpoints."""

    def setUp(self):
        """Create SACCOs, admin role, and API client."""
        self.client = APIClient()
        self.sacco = Sacco.objects.create(
            name='Alpha SACCO',
            registration_number='ALPHA001',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
        )
        self.other_sacco = Sacco.objects.create(
            name='Beta SACCO',
            registration_number='BETA001',
            sector=Sacco.Sector.TECHNOLOGY,
            county='Kiambu',
        )
        self.admin = User.objects.create_user(
            email='admin@example.com',
            password='StrongPass123',
            first_name='Admin',
            last_name='User',
        )
        Role.objects.create(
            user=self.admin,
            sacco=self.sacco,
            name=Role.SACCO_ADMIN,
        )
        self.client.force_authenticate(user=self.admin)

    def test_sacco_admin_sees_only_own_sacco_members(self):
        """Ensure a SACCO admin cannot retrieve another SACCO's member."""
        other_user = User.objects.create_user(
            email='other-member@example.com',
            password='StrongPass123',
            first_name='Other',
            last_name='Member',
        )
        other_membership = Membership.objects.create(
            user=other_user,
            sacco=self.other_sacco,
            status=Membership.Status.APPROVED,
            member_number='BETA-M001',
        )

        response = self.client.get(
            f'/api/v1/management/members/{other_membership.id}/',
            HTTP_X_SACCO_ID=str(self.sacco.id),
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_stats_correct_member_count(self):
        """Ensure stats count approved members in the admin's SACCO."""
        for index in range(5):
            user = User.objects.create_user(
                email=f'member-{index}@example.com',
                password='StrongPass123',
                first_name='Test',
                last_name=f'Member {index}',
            )
            Membership.objects.create(
                user=user,
                sacco=self.sacco,
                status=Membership.Status.APPROVED,
                member_number=f'ALPHA-M{index:03d}',
            )
        other_user = User.objects.create_user(
            email='other-count@example.com',
            password='StrongPass123',
            first_name='Other',
            last_name='Count',
        )
        Membership.objects.create(
            user=other_user,
            sacco=self.other_sacco,
            status=Membership.Status.APPROVED,
            member_number='BETA-M999',
        )

        response = self.client.get(
            '/api/v1/management/stats/',
            HTTP_X_SACCO_ID=str(self.sacco.id),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['total_members'], 5)

    def test_member_list_includes_top_level_user_id(self):
        """Ensure admin member list exposes user_id for frontend mapping."""
        user = User.objects.create_user(
            email='member@example.com',
            password='StrongPass123',
            first_name='List',
            last_name='Member',
        )
        Membership.objects.create(
            user=user,
            sacco=self.sacco,
            status=Membership.Status.APPROVED,
            member_number='ALPHA-M100',
        )

        response = self.client.get(
            '/api/v1/management/members/',
            HTTP_X_SACCO_ID=str(self.sacco.id),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        member = response.data['data']['results'][0]
        self.assertEqual(member['user_id'], str(user.id))

    def test_member_detail_includes_financial_dashboard_fields(self):
        """Ensure member detail includes savings and repayment metrics."""
        user = User.objects.create_user(
            email='detail-member@example.com',
            password='StrongPass123',
            first_name='Detail',
            last_name='Member',
        )
        membership = Membership.objects.create(
            user=user,
            sacco=self.sacco,
            status=Membership.Status.APPROVED,
            member_number='ALPHA-M101',
        )
        bosa_type = SavingsType.objects.create(
            sacco=self.sacco,
            name=SavingsType.Name.BOSA,
            minimum_contribution=Decimal('500.00'),
        )
        share_type = SavingsType.objects.create(
            sacco=self.sacco,
            name=SavingsType.Name.SHARE_CAPITAL,
            minimum_contribution=Decimal('1000.00'),
        )
        Saving.objects.create(
            membership=membership,
            savings_type=bosa_type,
            amount=Decimal('2500.00'),
            total_contributions=Decimal('1200.00'),
            last_transaction_date=timezone.localdate(),
            status=Saving.Status.ACTIVE,
        )
        Saving.objects.create(
            membership=membership,
            savings_type=share_type,
            amount=Decimal('3000.00'),
            total_contributions=Decimal('3000.00'),
            last_transaction_date=timezone.localdate(),
            status=Saving.Status.ACTIVE,
        )
        loan = Loan.objects.create(
            membership=membership,
            amount=Decimal('9000.00'),
            interest_rate=Decimal('12.00'),
            term_months=3,
            outstanding_balance=Decimal('6000.00'),
            status=Loan.Status.ACTIVE,
        )
        self._create_schedule_item(loan, 1, RepaymentSchedule.Status.PAID)
        self._create_schedule_item(loan, 2, RepaymentSchedule.Status.PAID)
        self._create_schedule_item(
            loan,
            3,
            RepaymentSchedule.Status.PENDING,
        )

        response = self.client.get(
            f'/api/v1/management/members/{membership.id}/',
            HTTP_X_SACCO_ID=str(self.sacco.id),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data['monthly_contribution'],
            Decimal('4200.00'),
        )
        self.assertEqual(response.data['share_capital'], Decimal('3000.00'))
        self.assertEqual(response.data['repayment_rate_pct'], 66.67)

    def test_disbursements_dashboard_returns_sacco_scoped_summary(self):
        """Ensure disbursement dashboard returns only current SACCO data."""
        membership = self._create_membership('disbursed@example.com')
        other_membership = self._create_membership(
            'other-disbursed@example.com',
            sacco=self.other_sacco,
            member_number='BETA-M200',
        )
        Loan.objects.create(
            membership=membership,
            amount=Decimal('10000.00'),
            disbursed_amount=Decimal('10000.00'),
            disbursement_date=timezone.localdate(),
            interest_rate=Decimal('12.00'),
            term_months=12,
            outstanding_balance=Decimal('10000.00'),
            status=Loan.Status.ACTIVE,
        )
        Loan.objects.create(
            membership=membership,
            amount=Decimal('5000.00'),
            interest_rate=Decimal('12.00'),
            term_months=6,
            outstanding_balance=Decimal('5000.00'),
            status=Loan.Status.APPROVED,
        )
        Loan.objects.create(
            membership=other_membership,
            amount=Decimal('20000.00'),
            disbursed_amount=Decimal('20000.00'),
            disbursement_date=timezone.localdate(),
            interest_rate=Decimal('12.00'),
            term_months=12,
            outstanding_balance=Decimal('20000.00'),
            status=Loan.Status.ACTIVE,
        )

        response = self.client.get(
            '/api/v1/management/dashboard/disbursements/',
            HTTP_X_SACCO_ID=str(self.sacco.id),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['disbursed_today']['count'], 1)
        self.assertEqual(
            response.data['disbursed_today']['total_amount'],
            Decimal('10000.00'),
        )
        self.assertEqual(response.data['pending_disbursement']['count'], 1)
        self.assertEqual(len(response.data['recent_disbursements']), 1)

    def test_contributions_dashboard_returns_sacco_scoped_summary(self):
        """Ensure contribution dashboard returns current SACCO totals."""
        membership = self._create_membership('contributor@example.com')
        missed_membership = self._create_membership(
            'missed@example.com',
            member_number='ALPHA-M301',
        )
        savings_type = SavingsType.objects.create(
            sacco=self.sacco,
            name=SavingsType.Name.BOSA,
            minimum_contribution=Decimal('1000.00'),
        )
        saving = Saving.objects.create(
            membership=membership,
            savings_type=savings_type,
            amount=Decimal('1500.00'),
            status=Saving.Status.ACTIVE,
        )
        Saving.objects.create(
            membership=missed_membership,
            savings_type=savings_type,
            amount=Decimal('0.00'),
            status=Saving.Status.ACTIVE,
        )
        transaction = Transaction.objects.create(
            user=membership.user,
            reference='TXN-CONTRIB-001',
            transaction_type=Transaction.TransactionType.DEPOSIT,
            amount=Decimal('1500.00'),
            status=Transaction.Status.COMPLETED,
        )
        MpesaTransaction.objects.create(
            transaction=transaction,
            phone_number='254712345678',
            checkout_request_id='CHECKOUT-CONTRIB-001',
            related_saving=saving,
        )

        response = self.client.get(
            '/api/v1/management/dashboard/contributions/',
            HTTP_X_SACCO_ID=str(self.sacco.id),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['received_today']['count'], 1)
        self.assertEqual(
            response.data['expected_this_month']['total_amount'],
            Decimal('2000.00'),
        )
        self.assertEqual(response.data['missed_overdue']['count'], 1)
        self.assertEqual(response.data['contribution_rate_pct'], 75.0)

    def test_application_approve_creates_membership(self):
        """Ensure approving an application creates an approved membership."""
        applicant = User.objects.create_user(
            email='applicant@example.com',
            password='StrongPass123',
            first_name='App',
            last_name='Licant',
        )
        application = SaccoApplication.objects.create(
            user=applicant,
            sacco=self.sacco,
            status=SaccoApplication.Status.SUBMITTED,
        )

        response = self.client.patch(
            f'/api/v1/management/applications/{application.id}/review/',
            {
                'status': SaccoApplication.Status.APPROVED,
                'review_notes': 'Welcome aboard.',
            },
            format='json',
            HTTP_X_SACCO_ID=str(self.sacco.id),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(
            Membership.objects.filter(
                user=applicant,
                sacco=self.sacco,
                status=Membership.Status.APPROVED,
            ).exists()
        )

    def _create_membership(
        self,
        email,
        sacco=None,
        member_number='ALPHA-M300',
    ):
        sacco = sacco or self.sacco
        user = User.objects.create_user(
            email=email,
            password='StrongPass123',
            first_name='Test',
            last_name='Member',
        )
        return Membership.objects.create(
            user=user,
            sacco=sacco,
            status=Membership.Status.APPROVED,
            member_number=member_number,
        )

    def _create_schedule_item(self, loan, instalment_number, item_status):
        return RepaymentSchedule.objects.create(
            loan=loan,
            instalment_number=instalment_number,
            due_date=timezone.localdate(),
            amount=Decimal('3000.00'),
            principal=Decimal('2800.00'),
            interest=Decimal('200.00'),
            balance_after=Decimal('0.00'),
            status=item_status,
        )


class ApplicationReviewViewTestCase(TestCase):
    """Test SaccoApplication review: rejection, idempotency, member
    numbering, permissions, and STAFF_ONLY enforcement on the apply path."""

    def setUp(self):
        self.client = APIClient()
        self.sacco = Sacco.objects.create(
            name='Review SACCO',
            registration_number='REVIEW001',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
        )
        self.admin = User.objects.create_user(
            email='review-admin@example.com',
            password='StrongPass123',
            first_name='Review',
            last_name='Admin',
        )
        Role.objects.create(
            user=self.admin,
            sacco=self.sacco,
            name=Role.SACCO_ADMIN,
        )
        self.applicant = User.objects.create_user(
            email='review-applicant@example.com',
            password='StrongPass123',
            first_name='App',
            last_name='Licant',
        )
        self.application = SaccoApplication.objects.create(
            user=self.applicant,
            sacco=self.sacco,
            status=SaccoApplication.Status.SUBMITTED,
        )
        Membership.objects.create(
            user=self.applicant,
            sacco=self.sacco,
            status=Membership.Status.PENDING,
        )
        self.review_url = (
            f'/api/v1/management/applications/{self.application.id}/review/'
        )

    def _review(self, review_status, notes='Reviewed.'):
        return self.client.patch(
            self.review_url,
            {'status': review_status, 'review_notes': notes},
            format='json',
            HTTP_X_SACCO_ID=str(self.sacco.id),
        )

    def test_reject_application_updates_membership_to_rejected(self):
        self.client.force_authenticate(user=self.admin)

        response = self._review(
            SaccoApplication.Status.REJECTED, 'Incomplete documents.',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        membership = Membership.objects.get(
            user=self.applicant, sacco=self.sacco,
        )
        self.assertEqual(membership.status, Membership.Status.REJECTED)
        self.assertEqual(
            membership.rejection_reason, 'Incomplete documents.',
        )
        self.assertTrue(
            SystemAuditLog.objects.filter(
                user=self.admin,
                action='APPLICATION_REJECTED',
                resource_type='SaccoApplication',
                resource_id=str(self.application.id),
            ).exists(),
        )

    def test_approve_application_assigns_member_number(self):
        self.client.force_authenticate(user=self.admin)

        response = self._review(SaccoApplication.Status.APPROVED)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        membership = Membership.objects.get(
            user=self.applicant, sacco=self.sacco,
        )
        self.assertEqual(membership.status, Membership.Status.APPROVED)
        self.assertTrue(membership.member_number)
        self.assertTrue(
            SystemAuditLog.objects.filter(
                user=self.admin,
                action='APPLICATION_APPROVED',
                resource_type='SaccoApplication',
                resource_id=str(self.application.id),
            ).exists(),
        )

    def test_rereview_already_approved_application_returns_409(self):
        self.client.force_authenticate(user=self.admin)
        self._review(SaccoApplication.Status.APPROVED)
        membership = Membership.objects.get(
            user=self.applicant, sacco=self.sacco,
        )
        member_number = membership.member_number
        notification_count = Notification.objects.filter(
            user=self.applicant,
        ).count()

        response = self._review(SaccoApplication.Status.APPROVED)

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        membership.refresh_from_db()
        self.assertEqual(membership.member_number, member_number)
        self.assertEqual(
            Notification.objects.filter(user=self.applicant).count(),
            notification_count,
        )

    def test_rereview_already_rejected_application_returns_409(self):
        self.client.force_authenticate(user=self.admin)
        self._review(SaccoApplication.Status.REJECTED)

        response = self._review(SaccoApplication.Status.REJECTED)

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)

    def test_non_admin_cannot_review_application(self):
        non_admin = User.objects.create_user(
            email='review-non-admin@example.com',
            password='StrongPass123',
            first_name='Not',
            last_name='Admin',
        )
        self.client.force_authenticate(user=non_admin)

        response = self._review(SaccoApplication.Status.APPROVED)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        membership = Membership.objects.get(
            user=self.applicant, sacco=self.sacco,
        )
        self.assertEqual(membership.status, Membership.Status.PENDING)

    def test_apply_to_staff_only_sacco_rejected(self):
        """
        No staff-verification mechanism exists anywhere in this codebase
        (confirmed by repo-wide search before implementing this), so
        STAFF_ONLY is treated the same as CLOSED for the public apply
        endpoint - there is no "qualifying staff applicant" case to test
        here, only rejection.
        """
        staff_only_sacco = Sacco.objects.create(
            name='Staff Only SACCO',
            registration_number='STAFFONLY001',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
            membership_type=Sacco.MembershipType.STAFF_ONLY,
        )
        serializer = MembershipApplySerializer(
            data={'sacco': str(staff_only_sacco.id)},
            context={
                'request': type('_Req', (), {'user': self.applicant})(),
            },
        )

        self.assertFalse(serializer.is_valid())
        self.assertIn('sacco', serializer.errors)
