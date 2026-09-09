"""Tests for the external-guarantor constraint, endpoint 400s, and sweep."""

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models import Sacco, User
from guarantor.models import ExternalGuarantor
from guarantor.tasks import expire_stale_external_guarantors_task
from notifications.models import Notification
from saccomembership.models import Membership
from services.models import Loan, LoanType


class ExternalGuarantorGroundTestCase(TestCase):
    def setUp(self):
        self.borrower = User.objects.create_user(
            email='eg-borrower@example.com',
            password='StrongPass1',
            first_name='Eg',
            last_name='Borrower',
            phone_number='254700004001',
        )
        self.sacco = Sacco.objects.create(
            name='EG Constraint SACCO',
            registration_number='EGC-001',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
        )
        self.membership = Membership.objects.create(
            user=self.borrower,
            sacco=self.sacco,
            status=Membership.Status.APPROVED,
            member_number='EGC-M-001',
            approved_date=timezone.now(),
        )
        self.loan_type = LoanType.objects.create(
            sacco=self.sacco,
            name='EG Loan',
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
            status=Loan.Status.GUARANTORS_PENDING,
        )
        self.client = APIClient()

    def _external(self, status_value, **overrides):
        defaults = dict(
            loan=self.loan,
            requested_by=self.borrower,
            sacco=self.sacco,
            full_name='Ext Guarantor',
            phone_number='254700004009',
            id_number='87654321',
            employment_status=ExternalGuarantor.EmploymentStatus.EMPLOYED,
            monthly_income=Decimal('80000.00'),
            guarantee_amount=Decimal('20000.00'),
            status=status_value,
        )
        defaults.update(overrides)
        return ExternalGuarantor.objects.create(**defaults)

    def _post_payload(self):
        return {
            'full_name': 'Ext Guarantor',
            'phone_number': '+254700004009',
            'id_number': '87654321',
            'employment_status': (
                ExternalGuarantor.EmploymentStatus.EMPLOYED
            ),
            'monthly_income': '80000.00',
            'guarantee_amount': '20000.00',
        }


@patch('guarantor.external_views.send_external_guarantor_sms_task.delay')
class ExternalGuarantorDuplicateConstraintTest(
    ExternalGuarantorGroundTestCase
):
    def test_duplicate_pending_submission_is_clean_400_not_500(
        self, _delay_mock,
    ):
        """A second submission while one is in-play -> 400 via the constraint.

        The serializer only blocks APPROVED_BY_ADMIN duplicates, so an
        SMS_SENT one slips past validation and the partial unique index
        must fire; the view catches the IntegrityError.
        """
        self._external(ExternalGuarantor.Status.SMS_SENT)
        self.client.force_authenticate(user=self.borrower)

        response = self.client.post(
            reverse(
                'services:external-guarantor-collection',
                kwargs={'loan_id': self.loan.id},
            ),
            self._post_payload(),
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('id_number', response.data)
        self.assertEqual(
            ExternalGuarantor.objects.filter(
                loan=self.loan, id_number='87654321',
            ).count(),
            1,
        )

    def test_resubmission_after_decline_is_allowed(self, _delay_mock):
        """A DECLINED / EXPIRED outcome frees the (loan, id_number) slot."""
        self._external(ExternalGuarantor.Status.DECLINED)
        self.client.force_authenticate(user=self.borrower)

        response = self.client.post(
            reverse(
                'services:external-guarantor-collection',
                kwargs={'loan_id': self.loan.id},
            ),
            self._post_payload(),
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(
            ExternalGuarantor.objects.filter(
                loan=self.loan, id_number='87654321',
            ).count(),
            2,
        )


class ExternalGuarantorExpirySweepTest(ExternalGuarantorGroundTestCase):
    def test_sweep_expires_stale_tokens_and_notifies_applicant(self):
        stale_sms = self._external(
            ExternalGuarantor.Status.SMS_SENT,
            id_number='11111111',
            response_token_expires_at=timezone.now() - timedelta(hours=1),
        )
        stale_pending = self._external(
            ExternalGuarantor.Status.PENDING_SMS,
            id_number='22222222',
            response_token_expires_at=timezone.now() - timedelta(minutes=5),
        )
        fresh = self._external(
            ExternalGuarantor.Status.SMS_SENT,
            id_number='33333333',
            response_token_expires_at=timezone.now() + timedelta(hours=10),
        )
        already_accepted = self._external(
            ExternalGuarantor.Status.ACCEPTED,
            id_number='44444444',
            response_token_expires_at=timezone.now() - timedelta(hours=2),
        )

        expired = expire_stale_external_guarantors_task()

        self.assertEqual(expired, 2)
        stale_sms.refresh_from_db()
        stale_pending.refresh_from_db()
        fresh.refresh_from_db()
        already_accepted.refresh_from_db()
        self.assertEqual(
            stale_sms.status, ExternalGuarantor.Status.EXPIRED,
        )
        self.assertEqual(
            stale_pending.status, ExternalGuarantor.Status.EXPIRED,
        )
        self.assertEqual(
            fresh.status, ExternalGuarantor.Status.SMS_SENT,
        )
        self.assertEqual(
            already_accepted.status, ExternalGuarantor.Status.ACCEPTED,
        )
        self.assertEqual(
            Notification.objects.filter(
                user=self.borrower,
                category=Notification.Category.GUARANTOR,
                title='Guarantor Request Expired',
            ).count(),
            2,
        )

    def test_expired_request_no_longer_blocks_loan_completion_gate(self):
        from guarantor.utils import check_loan_guarantors_complete

        self.loan.loan_type.requires_guarantors = False
        self.loan.loan_type.save(update_fields=['requires_guarantors'])
        self._external(
            ExternalGuarantor.Status.SMS_SENT,
            response_token_expires_at=timezone.now() - timedelta(hours=1),
        )

        blocked, _ = check_loan_guarantors_complete(self.loan)
        self.assertFalse(blocked)

        expire_stale_external_guarantors_task()

        unblocked, _ = check_loan_guarantors_complete(self.loan)
        self.assertTrue(unblocked)


@patch('guarantor.external_views.send_external_guarantor_sms_task.delay')
class ExternalGuarantorConsentLogTest(ExternalGuarantorGroundTestCase):
    def test_collecting_external_guarantor_pii_writes_consent_log(
        self, _delay_mock,
    ):
        from saccomanagement.models import DataConsentLog

        self.client.force_authenticate(user=self.borrower)
        response = self.client.post(
            reverse(
                'services:external-guarantor-collection',
                kwargs={'loan_id': self.loan.id},
            ),
            self._post_payload(),
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        external = ExternalGuarantor.objects.get(loan=self.loan)
        log = DataConsentLog.objects.get(data_type='EXTERNAL_GUARANTOR_PII')
        self.assertIsNone(log.user)
        self.assertEqual(log.accessed_by, self.borrower)
        self.assertIn(str(self.loan.id), log.reason)
        self.assertIn(str(external.id), log.reason)
