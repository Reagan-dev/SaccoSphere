"""Tests for audit and ODPC data access logging."""

from decimal import Decimal

from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models import Sacco, User
from saccomanagement.models import DataConsentLog, Role, SystemAuditLog
from saccomembership.models import Membership
from services.models import Loan, LoanType


class AuditLoggingTestCase(TestCase):
    """Test audit logs and data consent logs for admin actions."""

    def setUp(self):
        """Create a SACCO admin, member, and loan product."""
        self.client = APIClient()
        self.sacco = Sacco.objects.create(
            name='Audit SACCO',
            registration_number='AUD001',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
        )
        self.admin = User.objects.create_user(
            email='audit-admin@example.com',
            password='StrongPass123',
            first_name='Audit',
            last_name='Admin',
        )
        self.member = User.objects.create_user(
            email='audit-member@example.com',
            password='StrongPass123',
            first_name='Audit',
            last_name='Member',
        )
        Role.objects.create(
            user=self.admin,
            sacco=self.sacco,
            name=Role.SACCO_ADMIN,
        )
        self.membership = Membership.objects.create(
            user=self.member,
            sacco=self.sacco,
            status=Membership.Status.APPROVED,
            member_number='AUD-M001',
        )
        self.loan_type = LoanType.objects.create(
            sacco=self.sacco,
            name='Audit Loan',
            interest_rate=Decimal('12.00'),
            max_term_months=24,
            min_amount=Decimal('1000.00'),
            max_amount=Decimal('100000.00'),
            is_active=True,
        )
        self.client.force_authenticate(user=self.admin)

    def test_loan_approval_creates_audit_log(self):
        """Approving a loan should create an UPDATE audit log."""
        loan = Loan.objects.create(
            membership=self.membership,
            loan_type=self.loan_type,
            amount=Decimal('50000.00'),
            interest_rate=Decimal('12.00'),
            term_months=12,
            outstanding_balance=Decimal('0.00'),
            status=Loan.Status.BOARD_REVIEW,
        )

        response = self.client.patch(
            f'/api/v1/management/loans/{loan.id}/status/',
            {'status': Loan.Status.APPROVED},
            format='json',
            HTTP_X_SACCO_ID=str(self.sacco.id),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(
            SystemAuditLog.objects.filter(
                user=self.admin,
                action='UPDATE',
                resource_type='Loan',
                resource_id=str(loan.id),
            ).exists()
        )

    def test_member_view_creates_consent_log(self):
        """Viewing member detail should create a data consent log."""
        response = self.client.get(
            f'/api/v1/management/members/{self.membership.id}/',
            HTTP_X_SACCO_ID=str(self.sacco.id),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(
            DataConsentLog.objects.filter(
                user=self.member,
                accessed_by=self.admin,
                data_type='MEMBER_PROFILE',
            ).exists()
        )
