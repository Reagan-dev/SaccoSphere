"""B2C loan disbursement payout phone number.

initiate_b2c_loan_disbursement now defaults the M-Pesa B2C PartyB to the
member's own stored (OTP-verified) phone_number, resolved from the same
tenant-scoped loan.membership lookup as everything else in that
function - never from client input - and rejects a different number
outright. Paying a genuinely different number (lost SIM, etc.) is a
distinct, elevated-permission-gated action
(B2CDisbursementAlternateNumberView: IsSuperAdmin + a mandatory reason),
not a quiet field on the normal disbursement request.

Covers the fix for: any SACCO admin (or anyone who compromises one)
being able to silently redirect a loan disbursement by sending a
different phone_number in the normal disbursement request body.
"""

from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models import Sacco, SaccoPaymentConfig, User
from payments.disbursements import initiate_b2c_loan_disbursement
from payments.models import MpesaTransaction
from saccomanagement.models import Role
from saccomembership.models import Membership
from services.models import DisbursementAuditLog, Loan, LoanType


_B2C_OK = {
    'ConversationID': 'CONV-PHONE-1',
    'OriginatorConversationID': 'ORIG-PHONE-1',
}


def _make_disbursement_ready_loan(reg, member_email, member_phone):
    sacco = Sacco.objects.create(
        name=f'B2C Phone {reg}',
        registration_number=reg,
        sector=Sacco.Sector.FINANCE,
        county='Nairobi',
        membership_type=Sacco.MembershipType.OPEN,
        payment_ready=True,
    )
    SaccoPaymentConfig.objects.create(
        sacco=sacco,
        shortcode_type=SaccoPaymentConfig.ShortcodeType.PAYBILL,
        shortcode='600444',
        stk_passkey='phone_passkey',
        daraja_consumer_key='phone_consumer_key',
        daraja_consumer_secret='phone_consumer_secret',
        environment=SaccoPaymentConfig.Environment.SANDBOX,
        b2c_initiator_name='phone_initiator',
        b2c_security_credential='phone_security_credential',
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
        name='B2C Phone Loan',
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


class DefaultDisbursementPhoneNumberTest(TestCase):
    """The normal disbursement action always pays the member's own
    registered number, regardless of what the request body sends.

    Exercises initiate_b2c_loan_disbursement directly - the same
    function B2CDisbursementView.post calls with the request body's
    (optional) phone_number passed straight through as its keyword
    argument of the same name - mirroring how B2CDisbursementHardeningTests
    already tests this function, since a real HTTP round trip through
    this particular view needs a SACCO context this test harness's
    force_authenticate() cannot establish (a pre-existing, unrelated gap:
    SaccoContextMiddleware reads Django's own request.user, which
    force_authenticate() never touches).
    """

    def setUp(self):
        (
            self.sacco, self.member, self.membership, self.loan,
        ) = _make_disbursement_ready_loan(
            'B2CPH01', 'phone-member@example.com', '254712290001',
        )

    def test_defaults_to_member_number_when_omitted(self):
        with patch('payments.disbursements.DarajaClient') as client_cls:
            client = client_cls.return_value
            client._build_callback_url.return_value = 'https://cb.test/b2c'
            client.initiate_b2c.return_value = _B2C_OK

            success, _payload, http_status = initiate_b2c_loan_disbursement(
                loan=self.loan,
                amount=Decimal('500.00'),
                remarks='Loan Disbursement',
            )

        self.assertTrue(success)
        self.assertEqual(http_status, 201)
        _args, kwargs = client.initiate_b2c.call_args
        self.assertEqual(kwargs['phone_number'], '254712290001')

        mpesa = MpesaTransaction.objects.get(related_loan=self.loan)
        self.assertEqual(mpesa.phone_number, '+254712290001')

    def test_rejects_a_different_number_sent_by_the_caller(self):
        """A SACCO admin (or anyone who compromises one) sending a
        different phone_number in the request body must not be able to
        silently redirect the payout."""
        with patch('payments.disbursements.DarajaClient') as client_cls:
            client = client_cls.return_value

            success, payload, http_status = initiate_b2c_loan_disbursement(
                loan=self.loan,
                phone_number='+254799999999',
                amount=Decimal('500.00'),
                remarks='Loan Disbursement',
            )

        self.assertFalse(success)
        self.assertEqual(http_status, 400)
        self.assertIn("member's own registered number", payload['error'])
        client.initiate_b2c.assert_not_called()
        self.assertFalse(MpesaTransaction.objects.exists())
        self.loan.refresh_from_db()
        self.assertEqual(
            self.loan.disbursement_status, Loan.DisbursementStatus.PENDING,
        )
        self.assertFalse(
            DisbursementAuditLog.objects.filter(loan=self.loan).exists()
        )

    def test_succeeds_when_caller_sends_the_matching_member_number(self):
        """Backward compat: a caller that already sends the member's own
        (correct) number, in a differently-formatted way, is unaffected."""
        with patch('payments.disbursements.DarajaClient') as client_cls:
            client = client_cls.return_value
            client._build_callback_url.return_value = 'https://cb.test/b2c'
            client.initiate_b2c.return_value = _B2C_OK

            success, _payload, http_status = initiate_b2c_loan_disbursement(
                loan=self.loan,
                phone_number='0712290001',
                amount=Decimal('500.00'),
                remarks='Loan Disbursement',
            )

        self.assertTrue(success)
        self.assertEqual(http_status, 201)
        _args, kwargs = client.initiate_b2c.call_args
        self.assertEqual(kwargs['phone_number'], '254712290001')

        initiated_audit = DisbursementAuditLog.objects.get(
            loan=self.loan, event='B2C_INITIATED',
        )
        self.assertFalse(initiated_audit.details['alternate_number'])


class AlternateNumberPermissionTest(TestCase):
    """The alternate-number action requires IsSuperAdmin - a regular
    SACCO admin (the role a compromised account would most likely have)
    cannot authorize it alone."""

    def setUp(self):
        (
            self.sacco, self.member, self.membership, self.loan,
        ) = _make_disbursement_ready_loan(
            'B2CPH02', 'phone-member2@example.com', '254712290011',
        )
        self.sacco_admin = User.objects.create_user(
            email='phone-admin2@example.com',
            phone_number='254712290012',
            password='StrongPass1',
        )
        Role.objects.create(
            user=self.sacco_admin, sacco=self.sacco, name=Role.SACCO_ADMIN,
        )
        self.client = APIClient()

    def _payload(self):
        return {
            'loan_id': str(self.loan.id),
            'phone_number': '+254788888888',
            'amount': '500.00',
            'reason': (
                'Member reports a lost SIM card, confirmed by a call to '
                'their registered next of kin.'
            ),
        }

    def test_rejected_for_a_regular_sacco_admin(self):
        self.client.force_authenticate(user=self.sacco_admin)

        with patch('payments.disbursements.DarajaClient') as client_cls:
            response = self.client.post(
                reverse('payments:mpesa-b2c-disburse-alternate-number'),
                self._payload(),
                format='json',
            )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        client_cls.return_value.initiate_b2c.assert_not_called()
        self.loan.refresh_from_db()
        self.assertEqual(
            self.loan.disbursement_status, Loan.DisbursementStatus.PENDING,
        )
        self.assertFalse(
            DisbursementAuditLog.objects.filter(loan=self.loan).exists()
        )

    def test_rejected_when_unauthenticated(self):
        response = self.client.post(
            reverse('payments:mpesa-b2c-disburse-alternate-number'),
            self._payload(),
            format='json',
        )

        self.assertIn(
            response.status_code,
            (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN),
        )


class AlternateNumberAuthorizedTest(TestCase):
    """A super admin, with a reason, may disburse to a number other than
    the member's own - and it is fully captured in the audit trail."""

    def setUp(self):
        (
            self.sacco, self.member, self.membership, self.loan,
        ) = _make_disbursement_ready_loan(
            'B2CPH03', 'phone-member3@example.com', '254712290021',
        )
        self.super_admin = User.objects.create_user(
            email='phone-super@example.com',
            phone_number='254712290022',
            password='StrongPass1',
        )
        Role.objects.create(
            user=self.super_admin, sacco=None, name=Role.SUPER_ADMIN,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.super_admin)

    def test_missing_reason_is_rejected_by_serializer(self):
        response = self.client.post(
            reverse('payments:mpesa-b2c-disburse-alternate-number'),
            {
                'loan_id': str(self.loan.id),
                'phone_number': '+254788888888',
                'amount': '500.00',
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('reason', response.data['errors'])

    def test_succeeds_with_reason_and_records_reason_and_approver(self):
        with patch('payments.disbursements.DarajaClient') as client_cls:
            client = client_cls.return_value
            client._build_callback_url.return_value = 'https://cb.test/b2c'
            client.initiate_b2c.return_value = _B2C_OK

            response = self.client.post(
                reverse('payments:mpesa-b2c-disburse-alternate-number'),
                {
                    'loan_id': str(self.loan.id),
                    'phone_number': '+254788888888',
                    'amount': '500.00',
                    'reason': (
                        'Member reports a lost SIM card; identity '
                        'confirmed via a KYC callback. Paying to their '
                        'next-of-kin number on file.'
                    ),
                },
                format='json',
            )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        _args, kwargs = client.initiate_b2c.call_args
        self.assertEqual(kwargs['phone_number'], '254788888888')

        mpesa = MpesaTransaction.objects.get(related_loan=self.loan)
        self.assertEqual(mpesa.phone_number, '+254788888888')

        alt_audit = DisbursementAuditLog.objects.get(
            loan=self.loan,
            event='B2C_ALTERNATE_NUMBER_AUTHORIZED',
        )
        self.assertEqual(alt_audit.actor, self.super_admin)
        self.assertEqual(alt_audit.actor_role, 'super_admin')
        self.assertIn('lost SIM card', alt_audit.details['reason'])
        self.assertEqual(
            alt_audit.details['approver_id'], str(self.super_admin.id),
        )
        self.assertEqual(
            alt_audit.details['approver_email'], self.super_admin.email,
        )
        self.assertEqual(
            alt_audit.details['member_registered_number'], '254712290021',
        )
        self.assertEqual(
            alt_audit.details['alternate_number'], '+254788888888',
        )

        initiated_audit = DisbursementAuditLog.objects.get(
            loan=self.loan,
            event='B2C_INITIATED',
        )
        self.assertTrue(initiated_audit.details['alternate_number'])
        self.assertEqual(initiated_audit.actor, self.super_admin)
