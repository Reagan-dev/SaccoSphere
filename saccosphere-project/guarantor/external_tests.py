from decimal import Decimal

from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import Sacco, User
from guarantor.models import ExternalGuarantor
from saccomanagement.models import Role
from saccomembership.models import Membership
from services.models import Loan, LoanType


class ExternalGuarantorAdminReviewTest(APITestCase):
    def setUp(self):
        self.sacco = Sacco.objects.create(
            name='External Review SACCO',
            registration_number='EXT-REV-001',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
        )
        self.admin = User.objects.create_user(
            email='admin@example.com',
            password='StrongPass1',
        )
        self.non_admin = User.objects.create_user(
            email='member@example.com',
            password='StrongPass1',
        )
        self.applicant = User.objects.create_user(
            email='borrower@example.com',
            password='StrongPass1',
            first_name='Borrower',
            last_name='User',
        )
        Role.objects.create(
            user=self.admin,
            sacco=self.sacco,
            name=Role.SACCO_ADMIN,
        )
        self.membership = Membership.objects.create(
            user=self.applicant,
            sacco=self.sacco,
            status=Membership.Status.APPROVED,
            member_number='EXT-REV-MEM-001',
            approved_date=timezone.now(),
        )
        self.loan_type = LoanType.objects.create(
            sacco=self.sacco,
            name='Development Loan',
            interest_rate=Decimal('12.00'),
            max_term_months=12,
            min_amount=Decimal('1000.00'),
            max_amount=Decimal('100000.00'),
            requires_guarantors=True,
        )
        self.loan = Loan.objects.create(
            membership=self.membership,
            loan_type=self.loan_type,
            amount=Decimal('50000.00'),
            outstanding_balance=Decimal('50000.00'),
            interest_rate=Decimal('12.00'),
            term_months=12,
        )

    def create_external_guarantor(self, status_value):
        return ExternalGuarantor.objects.create(
            loan=self.loan,
            requested_by=self.applicant,
            sacco=self.sacco,
            full_name='External Person',
            phone_number='254700000005',
            id_number='12345678',
            employment_status=ExternalGuarantor.EmploymentStatus.EMPLOYED,
            monthly_income=Decimal('80000.00'),
            guarantee_amount=Decimal('50000.00'),
            status=status_value,
        )

    def test_admin_can_approve_accepted_guarantor(self):
        external_guarantor = self.create_external_guarantor(
            ExternalGuarantor.Status.ACCEPTED,
        )
        self.client.force_authenticate(user=self.admin)

        response = self.client.patch(
            reverse(
                'management:external-guarantor-review',
                kwargs={'pk': external_guarantor.pk},
            ),
            {'action': 'APPROVE'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        external_guarantor.refresh_from_db()
        self.assertEqual(
            external_guarantor.status,
            ExternalGuarantor.Status.APPROVED_BY_ADMIN,
        )
        self.assertEqual(external_guarantor.reviewed_by, self.admin)
        self.assertIsNotNone(external_guarantor.reviewed_at)

    def test_admin_cannot_approve_pending_sms_guarantor(self):
        external_guarantor = self.create_external_guarantor(
            ExternalGuarantor.Status.PENDING_SMS,
        )
        self.client.force_authenticate(user=self.admin)

        response = self.client.patch(
            reverse(
                'management:external-guarantor-review',
                kwargs={'pk': external_guarantor.pk},
            ),
            {'action': 'APPROVE'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        external_guarantor.refresh_from_db()
        self.assertEqual(
            external_guarantor.status,
            ExternalGuarantor.Status.PENDING_SMS,
        )

    def test_non_admin_cannot_review(self):
        external_guarantor = self.create_external_guarantor(
            ExternalGuarantor.Status.ACCEPTED,
        )
        self.client.force_authenticate(user=self.non_admin)

        response = self.client.patch(
            reverse(
                'management:external-guarantor-review',
                kwargs={'pk': external_guarantor.pk},
            ),
            {'action': 'APPROVE'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class LoanGuarantorGateTest(APITestCase):
    def setUp(self):
        self.sacco = Sacco.objects.create(
            name='Loan Gate SACCO',
            registration_number='LOAN-GATE-001',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
        )
        self.admin = User.objects.create_user(
            email='gate-admin@example.com',
            password='StrongPass1',
        )
        self.applicant = User.objects.create_user(
            email='gate-borrower@example.com',
            password='StrongPass1',
            first_name='Gate',
            last_name='Borrower',
        )
        Role.objects.create(
            user=self.admin,
            sacco=self.sacco,
            name=Role.SACCO_ADMIN,
        )
        self.membership = Membership.objects.create(
            user=self.applicant,
            sacco=self.sacco,
            status=Membership.Status.APPROVED,
            member_number='LOAN-GATE-MEM-001',
            approved_date=timezone.now(),
        )
        self.loan_type = LoanType.objects.create(
            sacco=self.sacco,
            name='Guaranteed Loan',
            interest_rate=Decimal('12.00'),
            max_term_months=12,
            min_amount=Decimal('1000.00'),
            max_amount=Decimal('100000.00'),
            requires_guarantors=True,
        )
        self.loan = Loan.objects.create(
            membership=self.membership,
            loan_type=self.loan_type,
            amount=Decimal('50000.00'),
            outstanding_balance=Decimal('50000.00'),
            interest_rate=Decimal('12.00'),
            term_months=12,
            status=Loan.Status.BOARD_REVIEW,
        )

    def create_external_guarantor(self, status_value):
        return ExternalGuarantor.objects.create(
            loan=self.loan,
            requested_by=self.applicant,
            sacco=self.sacco,
            full_name='External Person',
            phone_number='254700000006',
            id_number='87654321',
            employment_status=ExternalGuarantor.EmploymentStatus.EMPLOYED,
            monthly_income=Decimal('80000.00'),
            guarantee_amount=Decimal('50000.00'),
            status=status_value,
        )

    def test_loan_approval_blocked_if_external_guarantor_pending_review(self):
        self.create_external_guarantor(ExternalGuarantor.Status.ACCEPTED)
        self.client.force_authenticate(user=self.admin)

        response = self.client.patch(
            reverse('management:loan-approval', kwargs={'id': self.loan.id}),
            {'status': Loan.Status.APPROVED},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.loan.refresh_from_db()
        self.assertEqual(self.loan.status, Loan.Status.BOARD_REVIEW)

    def test_loan_approval_proceeds_when_all_guarantors_approved(self):
        self.create_external_guarantor(
            ExternalGuarantor.Status.APPROVED_BY_ADMIN,
        )
        self.client.force_authenticate(user=self.admin)

        response = self.client.patch(
            reverse('management:loan-approval', kwargs={'id': self.loan.id}),
            {'status': Loan.Status.APPROVED},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.loan.refresh_from_db()
        self.assertEqual(self.loan.status, Loan.Status.APPROVED)
