from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models import Sacco, User
from guarantor.models import ExternalGuarantor
from guarantor.utils import generate_response_token, send_guarantor_sms
from notifications.models import Notification
from saccomembership.models import Membership
from services.models import Loan, LoanType


class ExternalGuarantorTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email='borrower@example.com',
            password='StrongPass1',
            first_name='Borrower',
            last_name='User',
            phone_number='254700000004',
        )
        self.sacco = Sacco.objects.create(
            name='External Guarantor SACCO',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
        )
        self.membership = Membership.objects.create(
            user=self.user,
            sacco=self.sacco,
            status=Membership.Status.APPROVED,
            member_number='MEM-EXT-001',
        )
        self.loan_type = LoanType.objects.create(
            sacco=self.sacco,
            name='Development Loan',
            interest_rate=Decimal('12.00'),
            max_term_months=12,
            min_amount=Decimal('1000.00'),
            max_amount=Decimal('100000.00'),
        )
        self.loan = Loan.objects.create(
            membership=self.membership,
            loan_type=self.loan_type,
            amount=Decimal('50000.00'),
            outstanding_balance=Decimal('50000.00'),
            interest_rate=Decimal('12.00'),
            term_months=12,
        )
        self.client = APIClient()

    def create_external_guarantor(self):
        return ExternalGuarantor.objects.create(
            loan=self.loan,
            requested_by=self.user,
            sacco=self.sacco,
            full_name='External Person',
            phone_number='254700000005',
            id_number='12345678',
            employment_status=(
                ExternalGuarantor.EmploymentStatus.EMPLOYED
            ),
            monthly_income=Decimal('80000.00'),
            guarantee_amount=Decimal('25000.00'),
        )

    def test_generate_response_token_returns_url_safe_token(self):
        token = generate_response_token()

        self.assertEqual(len(token), 64)

    def test_external_guarantor_defaults_token_and_expiry(self):
        external_guarantor = self.create_external_guarantor()

        self.assertEqual(
            external_guarantor.status,
            ExternalGuarantor.Status.PENDING_SMS,
        )
        self.assertEqual(len(external_guarantor.response_token), 64)
        self.assertGreater(
            external_guarantor.response_token_expires_at,
            timezone.now(),
        )

    @patch('guarantor.utils.ATSMSClient')
    def test_send_guarantor_sms_sends_message_and_marks_sms_sent(
        self,
        sms_client,
    ):
        external_guarantor = self.create_external_guarantor()
        sms_client.return_value.send_sms.return_value = True

        sent = send_guarantor_sms(external_guarantor)

        self.assertTrue(sent)
        external_guarantor.refresh_from_db()
        self.assertEqual(
            external_guarantor.status,
            ExternalGuarantor.Status.SMS_SENT,
        )
        message = sms_client.return_value.send_sms.call_args.args[1]
        self.assertIn('External Person', message)
        self.assertIn(str(external_guarantor.response_token), message)
        self.assertIn('/guarantor/respond/', message)

    @patch('guarantor.external_views.send_guarantor_sms', return_value=True)
    def test_create_external_guarantor_endpoint(self, send_sms):
        self.client.force_authenticate(user=self.user)

        response = self.client.post(
            reverse(
                'services:external-guarantor-collection',
                kwargs={'loan_id': self.loan.id},
            ),
            {
                'full_name': 'External Person',
                'phone_number': '+254700000005',
                'id_number': '12345678',
                'employment_status': (
                    ExternalGuarantor.EmploymentStatus.EMPLOYED
                ),
                'monthly_income': '80000.00',
                'guarantee_amount': '25000.00',
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertNotIn('response_token', response.data)
        external_guarantor = ExternalGuarantor.objects.get(
            loan=self.loan,
            id_number='12345678',
        )
        self.assertEqual(
            external_guarantor.status,
            ExternalGuarantor.Status.SMS_SENT,
        )
        send_sms.assert_called_once_with(external_guarantor)
        self.assertTrue(
            Notification.objects.filter(
                user=self.user,
                category=Notification.Category.GUARANTOR,
                title='Guarantor SMS Sent',
            ).exists()
        )

    def test_list_external_guarantors_for_loan_owner(self):
        self.client.force_authenticate(user=self.user)
        external_guarantor = self.create_external_guarantor()

        response = self.client.get(
            reverse(
                'services:external-guarantor-collection',
                kwargs={'loan_id': self.loan.id},
            ),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data['results']
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['id'], external_guarantor.id)
        self.assertNotIn('response_token', results[0])

    def test_external_guarantor_accept_response_endpoint(self):
        external_guarantor = self.create_external_guarantor()
        external_guarantor.status = ExternalGuarantor.Status.SMS_SENT
        external_guarantor.save(update_fields=['status', 'updated_at'])

        response = self.client.post(
            reverse(
                'guarantor:external-guarantor-respond',
                kwargs={
                    'response_token': external_guarantor.response_token,
                },
            ),
            {'action': 'ACCEPT', 'notes': 'I agree.'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        external_guarantor.refresh_from_db()
        self.assertEqual(
            external_guarantor.status,
            ExternalGuarantor.Status.ACCEPTED,
        )
        self.assertEqual(
            external_guarantor.guarantor_response,
            ExternalGuarantor.GuarantorResponse.ACCEPTED,
        )
        self.assertIsNotNone(external_guarantor.guarantor_responded_at)
        self.assertTrue(
            Notification.objects.filter(
                user=self.user,
                category=Notification.Category.GUARANTOR,
            ).exists()
        )

    def test_external_guarantor_response_rejects_reuse(self):
        external_guarantor = self.create_external_guarantor()
        external_guarantor.status = ExternalGuarantor.Status.ACCEPTED
        external_guarantor.save(update_fields=['status', 'updated_at'])

        response = self.client.post(
            reverse(
                'guarantor:external-guarantor-respond',
                kwargs={
                    'response_token': external_guarantor.response_token,
                },
            ),
            {'action': 'DECLINE'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
