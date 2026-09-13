"""Tests for the SACCO admin operational report endpoint."""

from decimal import Decimal

from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models import Sacco, User
from payments.models import MpesaTransaction, Transaction
from saccomanagement.models import Role, SystemAuditLog
from saccomembership.models import Membership
from services.models import Loan, Saving, SavingsType


class SaccoReportViewTestCase(TestCase):
    """Test SACCO-scoped operational reports."""

    def setUp(self):
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
            email='report-admin@example.com',
            password='StrongPass123',
            first_name='Report',
            last_name='Admin',
        )
        Role.objects.create(
            user=self.admin,
            sacco=self.sacco,
            name=Role.SACCO_ADMIN,
        )
        self.client.force_authenticate(user=self.admin)

    def _create_membership(self, email, sacco=None, member_number='ALPHA-M1'):
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

    def test_requires_authentication(self):
        self.client.force_authenticate(user=None)

        response = self.client.get(
            '/api/v1/management/reports/?type=members',
            HTTP_X_SACCO_ID=str(self.sacco.id),
        )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_non_admin_is_forbidden(self):
        member = User.objects.create_user(
            email='plain-member@example.com',
            password='StrongPass123',
            first_name='Plain',
            last_name='Member',
        )
        self.client.force_authenticate(user=member)

        response = self.client.get('/api/v1/management/reports/?type=members')

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_missing_type_is_a_400(self):
        response = self.client.get(
            '/api/v1/management/reports/',
            HTTP_X_SACCO_ID=str(self.sacco.id),
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_invalid_type_is_a_400(self):
        response = self.client.get(
            '/api/v1/management/reports/?type=bogus',
            HTTP_X_SACCO_ID=str(self.sacco.id),
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_from_date_after_to_date_is_a_400(self):
        response = self.client.get(
            '/api/v1/management/reports/'
            '?type=members&from_date=2026-02-01&to_date=2026-01-01',
            HTTP_X_SACCO_ID=str(self.sacco.id),
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_multi_sacco_admin_without_header_gets_a_clean_400(self):
        """A read on this report is sensitive enough to require the header."""
        Role.objects.create(
            user=self.admin,
            sacco=self.other_sacco,
            name=Role.SACCO_ADMIN,
        )

        response = self.client.get('/api/v1/management/reports/?type=members')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            response.data['errors']['detail'].code,
            'sacco_header_required',
        )

    def test_loans_report_is_scoped_to_sacco_with_status_breakdown(self):
        membership = self._create_membership('borrower@example.com')
        other_membership = self._create_membership(
            'other-borrower@example.com',
            sacco=self.other_sacco,
            member_number='BETA-M1',
        )
        Loan.objects.create(
            membership=membership,
            amount=Decimal('10000.00'),
            interest_rate=Decimal('12.00'),
            term_months=12,
            outstanding_balance=Decimal('8000.00'),
            status=Loan.Status.ACTIVE,
        )
        Loan.objects.create(
            membership=membership,
            amount=Decimal('5000.00'),
            interest_rate=Decimal('12.00'),
            term_months=6,
            outstanding_balance=Decimal('5000.00'),
            status=Loan.Status.REJECTED,
        )
        Loan.objects.create(
            membership=other_membership,
            amount=Decimal('20000.00'),
            interest_rate=Decimal('12.00'),
            term_months=12,
            outstanding_balance=Decimal('20000.00'),
            status=Loan.Status.ACTIVE,
        )

        response = self.client.get(
            '/api/v1/management/reports/?type=loans',
            HTTP_X_SACCO_ID=str(self.sacco.id),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data['data']
        self.assertEqual(data['total_applications'], 2)
        self.assertEqual(data['active_count'], 1)
        self.assertEqual(data['rejected_count'], 1)
        # Compared as Decimal, not string: SQLite's SUM() over a
        # DecimalField column can drop trailing zeros ('15000' instead of
        # '15000.00'), which Postgres does not do - the view's own str()
        # call is otherwise correct, so this must not be a string match.
        self.assertEqual(
            Decimal(data['total_amount_requested']),
            Decimal('15000.00'),
        )

    def test_contributions_report_counts_only_completed_sacco_deposits(self):
        membership = self._create_membership('saver@example.com')
        savings_type = SavingsType.objects.create(
            sacco=self.sacco,
            name=SavingsType.Name.BOSA,
            minimum_contribution=Decimal('500.00'),
        )
        saving = Saving.objects.create(
            membership=membership,
            savings_type=savings_type,
            amount=Decimal('1500.00'),
            status=Saving.Status.ACTIVE,
        )
        completed = Transaction.objects.create(
            user=membership.user,
            sacco=self.sacco,
            reference='TXN-REPORT-COMPLETED',
            transaction_type=Transaction.TransactionType.DEPOSIT,
            amount=Decimal('1500.00'),
            status=Transaction.Status.COMPLETED,
        )
        MpesaTransaction.objects.create(
            transaction=completed,
            phone_number='254712345678',
            checkout_request_id='CHECKOUT-REPORT-1',
            related_saving=saving,
        )
        pending = Transaction.objects.create(
            user=membership.user,
            sacco=self.sacco,
            reference='TXN-REPORT-PENDING',
            transaction_type=Transaction.TransactionType.DEPOSIT,
            amount=Decimal('2000.00'),
            status=Transaction.Status.PENDING,
        )
        MpesaTransaction.objects.create(
            transaction=pending,
            phone_number='254712345678',
            checkout_request_id='CHECKOUT-REPORT-2',
            related_saving=saving,
        )

        response = self.client.get(
            '/api/v1/management/reports/?type=contributions',
            HTTP_X_SACCO_ID=str(self.sacco.id),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data['data']
        self.assertEqual(data['transaction_count'], 1)
        self.assertEqual(Decimal(data['total_amount']), Decimal('1500.00'))

    def test_members_report_counts_approved_and_pending(self):
        self._create_membership('approved@example.com', member_number='A-1')
        pending_user = User.objects.create_user(
            email='pending@example.com',
            password='StrongPass123',
            first_name='Pending',
            last_name='Member',
        )
        Membership.objects.create(
            user=pending_user,
            sacco=self.sacco,
            status=Membership.Status.PENDING,
            member_number='A-2',
        )
        self._create_membership(
            'other-sacco-member@example.com',
            sacco=self.other_sacco,
            member_number='B-1',
        )

        response = self.client.get(
            '/api/v1/management/reports/?type=members',
            HTTP_X_SACCO_ID=str(self.sacco.id),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data['data']
        self.assertEqual(data['total_members'], 1)
        self.assertEqual(data['approved_in_period'], 1)
        self.assertEqual(data['pending_in_period'], 1)

    def test_view_writes_an_audit_log_entry(self):
        response = self.client.get(
            '/api/v1/management/reports/?type=members',
            HTTP_X_SACCO_ID=str(self.sacco.id),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        entry = SystemAuditLog.objects.get(
            resource_type='SaccoReport',
            resource_id=str(self.sacco.id),
        )
        self.assertEqual(entry.user, self.admin)
        self.assertEqual(entry.action, 'VIEW')
        self.assertEqual(entry.new_values['type'], 'members')
