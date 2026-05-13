"""End-to-end integration tests for full member and payment journeys."""

from decimal import Decimal
from io import BytesIO
from unittest.mock import patch

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from PIL import Image
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from accounts.models import OTPToken, Sacco, User
from ledger.models import LedgerEntry
from notifications.models import Notification
from payments.models import MpesaTransaction
from payments.tasks import process_stk_callback_task
from saccomanagement.models import Role
from saccomembership.models import Membership, SaccoApplication
from services.models import Loan, LoanType, Saving, SavingsType


@override_settings(DEBUG=True, ALLOWED_HOSTS=['testserver', 'localhost'])
class MemberJourneyTest(APITestCase):
    """Full journey: register to SACCO application approval."""

    @classmethod
    def setUpTestData(cls):
        """Create shared SACCO setup for all member-journey test methods."""
        cls.sacco = Sacco.objects.create(
            name='E2E Member SACCO',
            registration_number='E2E-MEMBER-001',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
            membership_type=Sacco.MembershipType.OPEN,
            default_interest_rate=Decimal('12.00'),
            loan_multiplier=Decimal('3.00'),
            min_loan_months=0,
        )
        cls.admin = User.objects.create_user(
            email='e2e.admin@example.com',
            first_name='E2E',
            last_name='Admin',
            phone_number='254700000901',
            password='StrongPass1',
        )
        Role.objects.create(
            user=cls.admin,
            sacco=cls.sacco,
            name=Role.SACCO_ADMIN,
        )

    def setUp(self):
        """Initialize API clients and in-test step logs."""
        self.member_client = APIClient()
        self.admin_client = APIClient()
        self.step_logs = []

    def _log_step(self, step, response):
        """Store each step response for readable assertion failures."""
        self.step_logs.append(
            {
                'step': step,
                'status_code': response.status_code,
                'body': getattr(response, 'data', None),
            },
        )

    def _assert_no_server_or_client_error(self):
        """Assert no step returned a 4xx or 5xx response."""
        failures = [
            log for log in self.step_logs if log['status_code'] >= 400
        ]
        self.assertEqual(failures, [], msg=f'Flow failures: {failures}')

    def _build_test_image(self):
        """Create a valid in-memory PNG file for KYC upload."""
        image = Image.new('RGB', (600, 400), color='white')
        image_bytes = BytesIO()
        image.save(image_bytes, format='PNG')
        image_bytes.seek(0)
        return SimpleUploadedFile(
            'id_front.png',
            image_bytes.read(),
            content_type='image/png',
        )

    @patch('accounts.views.ATSMSClient.send_otp', return_value=True)
    @patch(
        'accounts.views.IPRSClient.verify_id',
        return_value={
            'verified': True,
            'id_number': '12345678',
            'name': 'Journey User',
            'iprs_reference': 'IPRS-E2E-REF',
        },
    )
    def test_full_member_onboarding_flow(self, _, __):
        """Run the full member onboarding journey from register to approval."""
        register_payload = {
            'email': 'journey.member@example.com',
            'first_name': 'Journey',
            'last_name': 'Member',
            'phone_number': '254700000123',
            'password': 'StrongPass1',
            'password2': 'StrongPass1',
        }
        register_response = self.member_client.post(
            '/api/v1/accounts/register/',
            register_payload,
            format='json',
        )
        self._log_step('register', register_response)
        self.assertEqual(register_response.status_code, status.HTTP_201_CREATED)
        user = User.objects.get(email=register_payload['email'])

        otp_send_response = self.member_client.post(
            '/api/v1/accounts/otp/send/',
            {
                'phone_number': register_payload['phone_number'],
                'purpose': OTPToken.Purpose.PHONE_VERIFY,
            },
            format='json',
        )
        self._log_step('otp-send', otp_send_response)
        self.assertEqual(otp_send_response.status_code, status.HTTP_200_OK)
        otp_token = OTPToken.objects.filter(
            phone_number=register_payload['phone_number'],
            purpose=OTPToken.Purpose.PHONE_VERIFY,
        ).latest('created_at')

        otp_verify_response = self.member_client.post(
            '/api/v1/accounts/otp/verify/',
            {
                'phone_number': register_payload['phone_number'],
                'code': otp_token.code,
            },
            format='json',
        )
        self._log_step('otp-verify', otp_verify_response)
        self.assertEqual(otp_verify_response.status_code, status.HTTP_200_OK)
        user.refresh_from_db()
        self.assertEqual(user.phone_number, register_payload['phone_number'])

        self.member_client.force_authenticate(user=user)
        kyc_upload_front_response = self.member_client.post(
            '/api/v1/accounts/kyc/upload/',
            {
                'document_type': 'id_front',
                'file': self._build_test_image(),
            },
            format='multipart',
        )
        self._log_step('kyc-upload-front', kyc_upload_front_response)
        self.assertEqual(
            kyc_upload_front_response.status_code,
            status.HTTP_200_OK,
        )
        kyc_upload_back_response = self.member_client.post(
            '/api/v1/accounts/kyc/upload/',
            {
                'document_type': 'id_back',
                'file': self._build_test_image(),
            },
            format='multipart',
        )
        self._log_step('kyc-upload-back', kyc_upload_back_response)
        self.assertEqual(
            kyc_upload_back_response.status_code,
            status.HTTP_200_OK,
        )
        user.kyc.refresh_from_db()
        self.assertTrue(bool(user.kyc.id_front))
        self.assertEqual(user.kyc.status, user.kyc.Status.PENDING)

        kyc_submit_response = self.member_client.post(
            '/api/v1/accounts/kyc/submit-id/',
            {
                'id_number': '12345678',
                'date_of_birth': '1990-01-01',
            },
            format='json',
        )
        self._log_step('kyc-submit-id', kyc_submit_response)
        self.assertEqual(kyc_submit_response.status_code, status.HTTP_200_OK)
        user.kyc.refresh_from_db()
        self.assertTrue(user.kyc.iprs_verified)

        membership_apply_response = self.member_client.post(
            '/api/v1/members/memberships/',
            {'sacco': str(self.sacco.id)},
            format='json',
        )
        self._log_step('membership-apply', membership_apply_response)
        self.assertEqual(
            membership_apply_response.status_code,
            status.HTTP_201_CREATED,
        )
        membership = Membership.objects.get(user=user, sacco=self.sacco)
        application = SaccoApplication.objects.get(user=user, sacco=self.sacco)
        self.assertEqual(membership.status, Membership.Status.PENDING)
        self.assertEqual(application.status, SaccoApplication.Status.SUBMITTED)

        self.admin_client.force_authenticate(user=self.admin)
        review_response = self.admin_client.patch(
            f'/api/v1/management/applications/{application.id}/review/',
            {'status': SaccoApplication.Status.APPROVED, 'review_notes': 'ok'},
            format='json',
            HTTP_X_SACCO_ID=str(self.sacco.id),
        )
        self._log_step('application-review', review_response)
        self.assertEqual(review_response.status_code, status.HTTP_200_OK)
        membership.refresh_from_db()
        self.assertEqual(membership.status, Membership.Status.APPROVED)

        self._assert_no_server_or_client_error()


@override_settings(DEBUG=True, ALLOWED_HOSTS=['testserver', 'localhost'])
class MpesaFlowTest(APITestCase):
    """Full M-Pesa STK push, callback, and balance update flow."""

    @classmethod
    def setUpTestData(cls):
        """Create baseline data for STK push integration flow."""
        cls.user = User.objects.create_user(
            email='mpesa.member@example.com',
            first_name='Mpesa',
            last_name='Member',
            phone_number='254711111111',
            password='StrongPass1',
        )
        cls.sacco = Sacco.objects.create(
            name='E2E Payment SACCO',
            registration_number='E2E-PAY-001',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
            membership_type=Sacco.MembershipType.OPEN,
            default_interest_rate=Decimal('12.00'),
            loan_multiplier=Decimal('3.00'),
            min_loan_months=0,
        )
        cls.membership = Membership.objects.create(
            user=cls.user,
            sacco=cls.sacco,
            status=Membership.Status.APPROVED,
            member_number='E2E-M001',
        )
        cls.savings_type = SavingsType.objects.create(
            sacco=cls.sacco,
            name=SavingsType.Name.BOSA,
            minimum_contribution=Decimal('0.00'),
        )
        cls.saving = Saving.objects.create(
            membership=cls.membership,
            savings_type=cls.savings_type,
            amount=Decimal('0.00'),
            total_contributions=Decimal('0.00'),
            status=Saving.Status.ACTIVE,
        )
        cls.loan_type = LoanType.objects.create(
            sacco=cls.sacco,
            name='E2E Loan',
            interest_rate=Decimal('12.00'),
            max_term_months=24,
            min_amount=Decimal('1000.00'),
            requires_guarantors=False,
        )
        cls.loan = Loan.objects.create(
            membership=cls.membership,
            loan_type=cls.loan_type,
            amount=Decimal('10000.00'),
            interest_rate=Decimal('12.00'),
            term_months=12,
            outstanding_balance=Decimal('10000.00'),
            status=Loan.Status.ACTIVE,
        )

    @patch(
        'payments.views.DarajaClient.initiate_stk_push',
        return_value={
            'MerchantRequestID': 'MRID-123',
            'CheckoutRequestID': 'CRID-123',
        },
    )
    @patch('payments.views.is_safaricom_ip', return_value=True)
    @patch('payments.views.is_replay_attack', return_value=False)
    @patch('payments.views.verify_mpesa_signature', return_value=True)
    @patch('payments.tasks.process_stk_callback_task.delay')
    def test_stk_push_to_balance_update(
        self,
        delay_mock,
        _signature_mock,
        _replay_mock,
        _ip_mock,
        _stk_mock,
    ):
        """Verify STK push through callback processing updates balances."""
        client = APIClient()
        client.force_authenticate(user=self.user)

        stk_push_response = client.post(
            '/api/v1/payments/mpesa/stk-push/',
            {
                'phone_number': '254711111111',
                'amount': '1000.00',
                'purpose': 'SAVING_DEPOSIT',
                'sacco_id': str(self.sacco.id),
                'saving_id': str(self.saving.id),
            },
            format='json',
        )
        self.assertEqual(stk_push_response.status_code, status.HTTP_201_CREATED)
        mpesa_transaction = MpesaTransaction.objects.get(
            checkout_request_id='CRID-123',
        )
        self.assertEqual(
            mpesa_transaction.transaction.status,
            mpesa_transaction.transaction.Status.PENDING,
        )

        callback_payload = {
            'Body': {
                'stkCallback': {
                    'MerchantRequestID': 'MRID-123',
                    'CheckoutRequestID': 'CRID-123',
                    'ResultCode': 0,
                    'ResultDesc': 'The service request is processed successfully.',
                    'CallbackMetadata': {
                        'Item': [
                            {'Name': 'Amount', 'Value': 1000},
                            {'Name': 'MpesaReceiptNumber', 'Value': 'QWE123RTY'},
                        ],
                    },
                },
            },
        }
        callback_response = client.post(
            '/api/v1/payments/callback/mpesa/stk/',
            callback_payload,
            format='json',
        )
        self.assertEqual(callback_response.status_code, status.HTTP_200_OK)
        delay_mock.assert_called_once()

        process_stk_callback_task(
            checkout_request_id='CRID-123',
            result_code=0,
            callback_body=callback_payload,
        )

        mpesa_transaction.refresh_from_db()
        self.saving.refresh_from_db()
        self.assertEqual(
            mpesa_transaction.transaction.status,
            mpesa_transaction.transaction.Status.COMPLETED,
        )
        self.assertEqual(self.saving.amount, Decimal('1000.00'))
        self.assertTrue(
            LedgerEntry.objects.filter(
                membership=self.membership,
                entry_type=LedgerEntry.EntryType.CREDIT,
                category=LedgerEntry.Category.SAVING_DEPOSIT,
            ).exists(),
        )
        self.assertTrue(
            Notification.objects.filter(user=self.user).exists(),
        )
