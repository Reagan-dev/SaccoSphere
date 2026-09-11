"""B2C loan disbursement amount: initiate_b2c_loan_disbursement always
disburses loan.amount and takes no amount parameter at all - so a
caller-declared amount that doesn't match the approved loan principal
must be rejected at the view layer, not silently ignored. Partial
disbursement is not a supported feature; if it becomes one, that is its
own project; this is just making sure a future "wire it up" maintainer
cannot accidentally let an arbitrary disbursement amount through.
"""

from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models import Sacco, SaccoPaymentConfig, User
from saccomanagement.models import Role
from saccomembership.models import Membership
from services.models import DisbursementAuditLog, Loan, LoanType


def _make_disbursement_ready_loan(reg, member_email, member_phone):
    sacco = Sacco.objects.create(
        name=f'B2C Amount {reg}',
        registration_number=reg,
        sector=Sacco.Sector.FINANCE,
        county='Nairobi',
        membership_type=Sacco.MembershipType.OPEN,
        payment_ready=True,
    )
    SaccoPaymentConfig.objects.create(
        sacco=sacco,
        shortcode_type=SaccoPaymentConfig.ShortcodeType.PAYBILL,
        shortcode='600666',
        stk_passkey='amount_passkey',
        daraja_consumer_key='amount_consumer_key',
        daraja_consumer_secret='amount_consumer_secret',
        environment=SaccoPaymentConfig.Environment.SANDBOX,
        b2c_initiator_name='amount_initiator',
        b2c_security_credential='amount_security_credential',
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
        name='B2C Amount Loan',
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


class DefaultDisbursementAmountMismatchTest(TestCase):
    """B2CDisbursementView (IsSaccoAdmin)."""

    def setUp(self):
        (
            self.sacco, self.member, self.membership, self.loan,
        ) = _make_disbursement_ready_loan(
            'B2CAMT01', 'amount-member@example.com', '254712297001',
        )
        self.admin = User.objects.create_user(
            email='amount-admin@example.com',
            phone_number='254712297002',
            password='StrongPass1',
        )
        Role.objects.create(
            user=self.admin, sacco=self.sacco, name=Role.SACCO_ADMIN,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.admin)

    def test_mismatched_amount_is_rejected_not_silently_ignored(self):
        with patch('payments.disbursements.DarajaClient') as client_cls:
            response = self.client.post(
                reverse('payments:mpesa-b2c-disburse'),
                {
                    'loan_id': str(self.loan.id),
                    # Approved principal is 500.00 - this must not be
                    # silently swapped in for it.
                    'amount': '50000.00',
                },
                format='json',
            )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('500.00', response.data['detail'])
        client_cls.return_value.initiate_b2c.assert_not_called()
        self.loan.refresh_from_db()
        self.assertEqual(
            self.loan.disbursement_status, Loan.DisbursementStatus.PENDING,
        )
        self.assertFalse(
            DisbursementAuditLog.objects.filter(loan=self.loan).exists()
        )

    def test_matching_amount_still_succeeds(self):
        with patch('payments.disbursements.DarajaClient') as client_cls:
            client = client_cls.return_value
            client._build_callback_url.return_value = 'https://cb.test/b2c'
            client.initiate_b2c.return_value = {
                'ConversationID': 'CONV-AMOUNT-OK',
                'OriginatorConversationID': 'ORIG-AMOUNT-OK',
            }

            response = self.client.post(
                reverse('payments:mpesa-b2c-disburse'),
                {
                    'loan_id': str(self.loan.id),
                    'amount': '500.00',
                },
                format='json',
            )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)


class AlternateNumberDisbursementAmountMismatchTest(TestCase):
    """B2CDisbursementAlternateNumberView (IsSuperAdmin) - the same
    check applies regardless of which action is disbursing."""

    def setUp(self):
        (
            self.sacco, self.member, self.membership, self.loan,
        ) = _make_disbursement_ready_loan(
            'B2CAMT02', 'amount-member2@example.com', '254712297011',
        )
        self.super_admin = User.objects.create_user(
            email='amount-super@example.com',
            phone_number='254712297012',
            password='StrongPass1',
        )
        Role.objects.create(
            user=self.super_admin, sacco=None, name=Role.SUPER_ADMIN,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.super_admin)

    def test_mismatched_amount_is_rejected_not_silently_ignored(self):
        with patch('payments.disbursements.DarajaClient') as client_cls:
            response = self.client.post(
                reverse('payments:mpesa-b2c-disburse-alternate-number'),
                {
                    'loan_id': str(self.loan.id),
                    'phone_number': '+254788888888',
                    'amount': '1.00',
                    'reason': (
                        'Member reports a lost SIM card, confirmed by a '
                        'call to their registered next of kin.'
                    ),
                },
                format='json',
            )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('500.00', response.data['detail'])
        client_cls.return_value.initiate_b2c.assert_not_called()
        self.loan.refresh_from_db()
        self.assertEqual(
            self.loan.disbursement_status, Loan.DisbursementStatus.PENDING,
        )
        self.assertFalse(
            DisbursementAuditLog.objects.filter(loan=self.loan).exists()
        )
