"""Tests for audit and ODPC data access logging."""

from decimal import Decimal

from django.test import RequestFactory, TestCase
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models import Sacco, User
from saccomanagement.audit_logger import log_audit
from saccomanagement.models import DataConsentLog, Role, SystemAuditLog
from saccomembership.models import Membership
from services.models import CRBCheck, Loan, LoanType


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
            requires_guarantors=False,
            is_active=True,
        )
        self.client.force_authenticate(user=self.admin)

    def test_loan_approval_creates_audit_log(self):
        """Approving a loan should create a LOAN_APPROVED audit log."""
        loan = Loan.objects.create(
            membership=self.membership,
            loan_type=self.loan_type,
            amount=Decimal('50000.00'),
            interest_rate=Decimal('12.00'),
            term_months=12,
            outstanding_balance=Decimal('0.00'),
            status=Loan.Status.UNDER_REVIEW,
        )
        CRBCheck.objects.create(
            loan=loan,
            score=700,
            band=CRBCheck.CreditBand.GOOD,
            listed_negative=False,
            provider='metropol',
            reference='AUD-CRB-REF001',
            checked_by=self.admin,
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
                action='LOAN_APPROVED',
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


class LogAuditClientIPTestCase(TestCase):
    """log_audit used to resolve SystemAuditLog.ip_address with its own
    copy of a leftmost-X-Forwarded-For reader (spoofable); it now
    delegates to the canonical config.utils.get_client_ip."""

    def setUp(self):
        self.factory = RequestFactory()
        self.admin = User.objects.create_user(
            email='audit-ip-admin@example.com',
            password='StrongPass123',
        )

    def test_spoofed_leftmost_forwarded_for_entry_is_not_trusted(self):
        request = self.factory.post(
            '/', HTTP_X_FORWARDED_FOR='41.90.64.9, 203.0.113.5',
        )

        log = log_audit(
            self.admin,
            'LOAN_APPROVED',
            'Loan',
            'some-loan-id',
            request=request,
        )

        self.assertEqual(log.ip_address, '203.0.113.5')
        self.assertNotEqual(log.ip_address, '41.90.64.9')

    def test_falls_back_to_remote_addr_without_forwarded_header(self):
        request = self.factory.post('/', REMOTE_ADDR='192.0.2.9')

        log = log_audit(
            self.admin,
            'LOAN_APPROVED',
            'Loan',
            'some-loan-id',
            request=request,
        )

        self.assertEqual(log.ip_address, '192.0.2.9')

    def test_no_request_records_no_ip(self):
        log = log_audit(
            self.admin,
            'LOAN_APPROVED',
            'Loan',
            'some-loan-id',
        )

        self.assertIsNone(log.ip_address)
