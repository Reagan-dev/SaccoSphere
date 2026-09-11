"""B2C disbursement audit-log client IP resolution.

initiate_b2c_loan_disbursement used to carry its own copy of a
leftmost-X-Forwarded-For IP resolver (disbursements._get_ip) - the same
weak, spoofable logic payments.integrations.mpesa.security used to have
before it was replaced by the canonical, Railway-aware
config.utils.get_client_ip (see test_mpesa_security.py). That local copy
is gone; DisbursementAuditLog.ip_address is now always resolved through
the canonical function, so a forged leading X-Forwarded-For entry can
never be recorded as the actor's IP.
"""

from decimal import Decimal
from unittest.mock import patch

from django.test import RequestFactory, TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from accounts.models import Sacco, SaccoPaymentConfig, User
from payments.disbursements import initiate_b2c_loan_disbursement
from saccomanagement.models import Role
from saccomembership.models import Membership
from services.models import DisbursementAuditLog, Loan, LoanType


_B2C_OK = {
    'ConversationID': 'CONV-IP-1',
    'OriginatorConversationID': 'ORIG-IP-1',
}

SPOOFED_IP = '41.90.64.9'
REAL_IP = '203.0.113.5'


def _make_disbursement_ready_loan(reg, member_email, member_phone):
    sacco = Sacco.objects.create(
        name=f'B2C IP {reg}',
        registration_number=reg,
        sector=Sacco.Sector.FINANCE,
        county='Nairobi',
        membership_type=Sacco.MembershipType.OPEN,
        payment_ready=True,
    )
    SaccoPaymentConfig.objects.create(
        sacco=sacco,
        shortcode_type=SaccoPaymentConfig.ShortcodeType.PAYBILL,
        shortcode='600777',
        stk_passkey='ip_passkey',
        daraja_consumer_key='ip_consumer_key',
        daraja_consumer_secret='ip_consumer_secret',
        environment=SaccoPaymentConfig.Environment.SANDBOX,
        b2c_initiator_name='ip_initiator',
        b2c_security_credential='ip_security_credential',
        is_active=True,
    )
    member = User.objects.create_user(
        email=member_email,
        phone_number=member_phone,
        password='StrongPass1',
    )
    membership = Membership.objects.create(
        user=member,
        sacco=sacco,
        status=Membership.Status.APPROVED,
        member_number=f'{reg}-M-001',
    )
    loan_type = LoanType.objects.create(
        sacco=sacco,
        name='B2C IP Loan',
        interest_rate=Decimal('12.00'),
        max_term_months=12,
        min_amount=Decimal('100.00'),
        requires_guarantors=False,
    )
    loan = Loan.objects.create(
        membership=membership,
        loan_type=loan_type,
        amount=Decimal('500.00'),
        interest_rate=Decimal('12.00'),
        term_months=6,
        outstanding_balance=Decimal('0.00'),
        status=Loan.Status.APPROVED,
        disbursement_status=Loan.DisbursementStatus.PENDING,
    )
    return sacco, member, membership, loan


class DisbursementFunctionAuditLogIPTest(TestCase):
    """initiate_b2c_loan_disbursement records the caller's real IP, never
    a spoofed leftmost X-Forwarded-For entry."""

    def setUp(self):
        (
            self.sacco, self.member, self.membership, self.loan,
        ) = _make_disbursement_ready_loan(
            'B2CIP01', 'ip-member@example.com', '254712298001',
        )
        self.factory = RequestFactory()

    def _initiate(self, request=None):
        with patch('payments.disbursements.DarajaClient') as client_cls:
            client = client_cls.return_value
            client._build_callback_url.return_value = 'https://cb.test/b2c'
            client.initiate_b2c.return_value = _B2C_OK
            return initiate_b2c_loan_disbursement(
                loan=self.loan,
                remarks='Loan Disbursement',
                request=request,
            )

    def test_spoofed_leftmost_forwarded_for_entry_is_not_trusted(self):
        # Railway's edge appends the real peer; only the rightmost entry
        # is trustworthy. The attacker-controlled leftmost entry must
        # never end up in the audit trail.
        request = self.factory.post(
            '/', HTTP_X_FORWARDED_FOR=f'{SPOOFED_IP}, {REAL_IP}',
        )

        success, _payload, _http_status = self._initiate(request)

        self.assertTrue(success)
        audit = DisbursementAuditLog.objects.get(
            loan=self.loan, event='B2C_INITIATED',
        )
        self.assertEqual(audit.ip_address, REAL_IP)
        self.assertNotEqual(audit.ip_address, SPOOFED_IP)

    def test_falls_back_to_remote_addr_without_forwarded_header(self):
        request = self.factory.post('/', REMOTE_ADDR='192.0.2.9')

        success, _payload, _http_status = self._initiate(request)

        self.assertTrue(success)
        audit = DisbursementAuditLog.objects.get(
            loan=self.loan, event='B2C_INITIATED',
        )
        self.assertEqual(audit.ip_address, '192.0.2.9')

    def test_no_request_records_no_ip(self):
        success, _payload, _http_status = self._initiate(request=None)

        self.assertTrue(success)
        audit = DisbursementAuditLog.objects.get(
            loan=self.loan, event='B2C_INITIATED',
        )
        self.assertIsNone(audit.ip_address)


class B2CDisbursementViewAuditLogIPTest(TestCase):
    """Same guarantee, exercised through the real HTTP request path
    (B2CDisbursementView.post), not just a bare RequestFactory request
    handed straight to the function."""

    def setUp(self):
        (
            self.sacco, self.member, self.membership, self.loan,
        ) = _make_disbursement_ready_loan(
            'B2CIP02', 'ip-member2@example.com', '254712298011',
        )
        self.admin = User.objects.create_user(
            email='ip-admin@example.com',
            phone_number='254712298012',
            password='StrongPass1',
        )
        Role.objects.create(
            user=self.admin, sacco=self.sacco, name=Role.SACCO_ADMIN,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.admin)

    def test_spoofed_leftmost_forwarded_for_entry_is_not_trusted(self):
        with patch('payments.disbursements.DarajaClient') as client_cls:
            client = client_cls.return_value
            client._build_callback_url.return_value = 'https://cb.test/b2c'
            client.initiate_b2c.return_value = _B2C_OK

            response = self.client.post(
                reverse('payments:mpesa-b2c-disburse'),
                {
                    'loan_id': str(self.loan.id),
                    'amount': '500.00',
                },
                format='json',
                HTTP_X_FORWARDED_FOR=f'{SPOOFED_IP}, {REAL_IP}',
            )

        self.assertEqual(response.status_code, 201)
        audit = DisbursementAuditLog.objects.get(
            loan=self.loan, event='B2C_INITIATED',
        )
        self.assertEqual(audit.ip_address, REAL_IP)
        self.assertNotEqual(audit.ip_address, SPOOFED_IP)
