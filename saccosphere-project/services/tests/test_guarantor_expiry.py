"""Expiry sweep for stale internal (in-app) guarantor requests.

Mirrors guarantor/test_tasks.py's ExternalGuarantorExpirySweepTest - an
internal guarantor who never responds should stop counting as "pending"
forever and the applicant should be told to add another one, the same
courtesy already given for external (SMS) guarantors.
"""

from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from accounts.models import Sacco, User
from notifications.models import Notification
from saccomembership.models import Membership
from services.models import Guarantor, Loan, LoanType
from services.tasks import expire_stale_internal_guarantors_task


class InternalGuarantorExpirySweepTest(TestCase):
    def setUp(self):
        self.applicant = User.objects.create_user(
            email='ig-applicant@example.com',
            password='StrongPass1',
            first_name='Ig',
            last_name='Applicant',
        )
        self.guarantor_user = User.objects.create_user(
            email='ig-guarantor@example.com',
            password='StrongPass1',
            first_name='Good',
            last_name='Guarantor',
        )
        self.sacco = Sacco.objects.create(
            name='IG Expiry SACCO',
            registration_number='IGX-001',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
        )
        self.applicant_membership = Membership.objects.create(
            user=self.applicant,
            sacco=self.sacco,
            status=Membership.Status.APPROVED,
            member_number='IGX-M-001',
            approved_date=timezone.now(),
        )
        self.loan_type = LoanType.objects.create(
            sacco=self.sacco,
            name='IG Expiry Loan',
            interest_rate=Decimal('12.00'),
            max_term_months=12,
            min_amount=Decimal('1000.00'),
        )
        self.loan = Loan.objects.create(
            membership=self.applicant_membership,
            loan_type=self.loan_type,
            amount=Decimal('30000.00'),
            interest_rate=Decimal('12.00'),
            term_months=12,
            outstanding_balance=Decimal('30000.00'),
            status=Loan.Status.PENDING,
        )

    def _guarantor(self, **overrides):
        defaults = dict(
            loan=self.loan,
            guarantor=self.guarantor_user,
            guarantee_amount=Decimal('10000.00'),
            status=Guarantor.Status.PENDING,
        )
        defaults.update(overrides)
        return Guarantor.objects.create(**defaults)

    def test_sweep_expires_stale_requests_and_notifies_applicant(self):
        stale = self._guarantor(
            expires_at=timezone.now() - timedelta(hours=1),
        )
        fresh = self._guarantor(
            guarantor=User.objects.create_user(
                email='ig-fresh@example.com', password='StrongPass1',
            ),
            expires_at=timezone.now() + timedelta(hours=10),
        )
        already_approved = self._guarantor(
            guarantor=User.objects.create_user(
                email='ig-approved@example.com', password='StrongPass1',
            ),
            status=Guarantor.Status.APPROVED,
            expires_at=timezone.now() - timedelta(hours=2),
        )
        grandfathered = self._guarantor(
            guarantor=User.objects.create_user(
                email='ig-grandfathered@example.com',
                password='StrongPass1',
            ),
            expires_at=None,
        )

        expired = expire_stale_internal_guarantors_task()

        self.assertEqual(expired, 1)
        stale.refresh_from_db()
        fresh.refresh_from_db()
        already_approved.refresh_from_db()
        grandfathered.refresh_from_db()
        self.assertEqual(stale.status, Guarantor.Status.EXPIRED)
        self.assertEqual(fresh.status, Guarantor.Status.PENDING)
        self.assertEqual(already_approved.status, Guarantor.Status.APPROVED)
        self.assertEqual(grandfathered.status, Guarantor.Status.PENDING)
        self.assertEqual(
            Notification.objects.filter(
                user=self.applicant,
                title='Guarantor Request Expired',
            ).count(),
            1,
        )

    def test_expiring_one_guarantor_does_not_touch_loan_status(self):
        self._guarantor(expires_at=timezone.now() - timedelta(hours=1))
        self.loan.status = Loan.Status.PENDING_APPROVAL
        self.loan.save(update_fields=['status'])

        expire_stale_internal_guarantors_task()

        self.loan.refresh_from_db()
        self.assertEqual(self.loan.status, Loan.Status.PENDING_APPROVAL)

    def test_expired_guarantor_can_no_longer_be_responded_to(self):
        guarantor = self._guarantor(
            expires_at=timezone.now() - timedelta(hours=1),
        )

        expire_stale_internal_guarantors_task()

        guarantor.refresh_from_db()
        self.assertNotEqual(guarantor.status, Guarantor.Status.PENDING)
