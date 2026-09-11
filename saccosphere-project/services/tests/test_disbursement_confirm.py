"""Confirm / dispute disbursement endpoints must be POST, not GET.

A plain GET that mutates state is fired automatically by link-prefetch
bots (mail scanners, chat unfurlers, antivirus), which would silently
confirm or dispute a member's disbursement. These endpoints are now
POST-only with the signed token carried in the request body.

Also covers: ConfirmDisbursementView / DisputeDisbursementView used to
resolve DisbursementAuditLog.ip_address with their own copy of a
leftmost-X-Forwarded-For reader (spoofable); they now delegate to the
canonical config.utils.get_client_ip like every other IP-resolving call
site in the project.
"""

from decimal import Decimal
from unittest.mock import patch

from django.core.signing import TimestampSigner
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from accounts.models import Sacco, User
from saccomembership.models import Membership
from services.models import DisbursementAuditLog, Loan, LoanType


class DisbursementConfirmDisputeMethodTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            email='confirm-member@example.com',
            phone_number='254712270001',
            password='StrongPass1',
        )
        self.sacco = Sacco.objects.create(
            name='Confirm SACCO',
            registration_number='CONF-001',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
            membership_type=Sacco.MembershipType.OPEN,
        )
        self.membership = Membership.objects.create(
            user=self.user,
            sacco=self.sacco,
            status=Membership.Status.APPROVED,
            member_number='CONF-M-001',
        )
        loan_type = LoanType.objects.create(
            sacco=self.sacco,
            name='Confirm Loan',
            interest_rate=Decimal('12.00'),
            max_term_months=12,
            min_amount=Decimal('100.00'),
        )
        self.loan = Loan.objects.create(
            membership=self.membership,
            loan_type=loan_type,
            amount=Decimal('500.00'),
            interest_rate=Decimal('12.00'),
            term_months=6,
            outstanding_balance=Decimal('500.00'),
            status=Loan.Status.ACTIVE,
            disbursement_status=Loan.DisbursementStatus.DISBURSED,
        )
        self.token = TimestampSigner().sign(str(self.loan.id))
        self.confirm_url = reverse('services:confirm-disbursement')
        self.dispute_url = reverse('services:dispute-disbursement')

    def test_get_is_not_allowed_on_confirm(self):
        response = self.client.get(
            self.confirm_url,
            {'token': self.token},
        )
        self.assertEqual(response.status_code, 405)
        self.loan.refresh_from_db()
        self.assertEqual(
            self.loan.disbursement_status,
            Loan.DisbursementStatus.DISBURSED,
        )

    def test_get_is_not_allowed_on_dispute(self):
        response = self.client.get(
            self.dispute_url,
            {'token': self.token},
        )
        self.assertEqual(response.status_code, 405)

    @patch('services.views._record_disbursement_invoice_item')
    def test_post_confirms_disbursement(self, _invoice_mock):
        response = self.client.post(
            self.confirm_url,
            {'token': self.token},
            format='json',
        )
        self.assertEqual(response.status_code, 200)
        self.loan.refresh_from_db()
        self.assertEqual(
            self.loan.disbursement_status,
            Loan.DisbursementStatus.MEMBER_CONFIRMED,
        )
        self.assertTrue(
            DisbursementAuditLog.objects.filter(
                loan=self.loan,
                event='MEMBER_CONFIRMED',
            ).exists()
        )

    @patch('services.views._record_disbursement_invoice_item')
    def test_post_confirm_records_trusted_ip_not_spoofed_leftmost(
        self, _invoice_mock,
    ):
        # ConfirmDisbursementView used to resolve ip_address with its own
        # copy of a leftmost-X-Forwarded-For reader; it now delegates to
        # config.utils.get_client_ip, which trusts only the rightmost
        # (proxy-appended) entry.
        response = self.client.post(
            self.confirm_url,
            {'token': self.token},
            format='json',
            HTTP_X_FORWARDED_FOR='41.90.64.9, 203.0.113.5',
        )
        self.assertEqual(response.status_code, 200)
        audit = DisbursementAuditLog.objects.get(
            loan=self.loan, event='MEMBER_CONFIRMED',
        )
        self.assertEqual(audit.ip_address, '203.0.113.5')
        self.assertNotEqual(audit.ip_address, '41.90.64.9')

    def test_post_without_token_is_rejected(self):
        response = self.client.post(self.confirm_url, {}, format='json')
        self.assertEqual(response.status_code, 400)

    def test_post_with_bad_token_is_rejected(self):
        response = self.client.post(
            self.confirm_url,
            {'token': 'not-a-valid-token'},
            format='json',
        )
        self.assertEqual(response.status_code, 400)
        self.loan.refresh_from_db()
        self.assertEqual(
            self.loan.disbursement_status,
            Loan.DisbursementStatus.DISBURSED,
        )

    @patch('services.views._notify_sacco_admins')
    @patch('services.views._notify_superadmins')
    def test_post_disputes_disbursement(self, _super_mock, _sacco_mock):
        response = self.client.post(
            self.dispute_url,
            {'token': self.token, 'reason': 'Nothing arrived'},
            format='json',
        )
        self.assertEqual(response.status_code, 200)
        self.loan.refresh_from_db()
        self.assertEqual(
            self.loan.disbursement_status,
            Loan.DisbursementStatus.DISPUTED,
        )
        self.assertEqual(self.loan.dispute_reason, 'Nothing arrived')
        self.assertTrue(
            DisbursementAuditLog.objects.filter(
                loan=self.loan,
                event='MEMBER_DISPUTED',
            ).exists()
        )

    @patch('services.views._notify_sacco_admins')
    @patch('services.views._notify_superadmins')
    def test_post_dispute_records_trusted_ip_not_spoofed_leftmost(
        self, _super_mock, _sacco_mock,
    ):
        response = self.client.post(
            self.dispute_url,
            {'token': self.token, 'reason': 'Nothing arrived'},
            format='json',
            HTTP_X_FORWARDED_FOR='41.90.64.9, 203.0.113.5',
        )
        self.assertEqual(response.status_code, 200)
        audit = DisbursementAuditLog.objects.get(
            loan=self.loan, event='MEMBER_DISPUTED',
        )
        self.assertEqual(audit.ip_address, '203.0.113.5')
        self.assertNotEqual(audit.ip_address, '41.90.64.9')
