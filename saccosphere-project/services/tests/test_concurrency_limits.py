"""Concurrency regression tests for loan eligibility and guarantor capacity.

Two near-simultaneous requests from/for one member must not jointly
exceed a limit by both reading a stale pre-lock snapshot. The threaded
cases need real row-level locking, so they run on PostgreSQL and skip on
SQLite (select_for_update is a no-op there and concurrent writers just
raise "database is locked"), matching
accounts.tests.test_otp_security.OTPRaceConditionTestCase. Deterministic
sequential counterparts prove the same invariant on every backend.
"""

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal

from django.db import connection
from django.db.models import Sum
from django.test import TransactionTestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import Sacco, User
from saccomembership.models import Membership
from services.engines.guarantor_logic import update_guarantee_capacity
from services.models import (
    Guarantor,
    Loan,
    LoanType,
    Saving,
    SavingsType,
)


def _approved_member(sacco, email, member_number, phone):
    user = User.objects.create_user(
        email=email, phone_number=phone, password='testpass123',
    )
    membership = Membership.objects.create(
        user=user,
        sacco=sacco,
        status=Membership.Status.APPROVED,
        member_number=member_number,
        approved_date=timezone.now() - timedelta(days=200),
    )
    return user, membership


class LoanEligibilityConcurrencyTests(TransactionTestCase):
    """Two applications from one member can't jointly exceed the limit."""

    def setUp(self):
        self.sacco = Sacco.objects.create(
            name='Eligibility Race SACCO',
            registration_number='ELR001',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
            membership_type=Sacco.MembershipType.OPEN,
            loan_multiplier=Decimal('1.00'),
            min_loan_months=0,
        )
        self.user, self.membership = _approved_member(
            self.sacco, 'elig-race@example.com', 'ELR-M-001', '254712810001',
        )
        savings_type = SavingsType.objects.create(
            sacco=self.sacco,
            name=SavingsType.Name.BOSA,
            minimum_contribution=Decimal('100.00'),
        )
        Saving.objects.create(
            membership=self.membership,
            savings_type=savings_type,
            amount=Decimal('10000.00'),
            status=Saving.Status.ACTIVE,
        )
        self.loan_type = LoanType.objects.create(
            sacco=self.sacco,
            name='Race Loan',
            interest_rate=Decimal('12.00'),
            max_term_months=36,
            min_amount=Decimal('100.00'),
            requires_guarantors=False,
        )
        self.url = reverse('services:loan-apply')

    def _apply(self):
        client = APIClient()
        client.force_authenticate(user=self.user)
        return client.post(
            self.url,
            {
                'loan_type': str(self.loan_type.id),
                'amount': '7000.00',
                'term_months': 6,
            },
            format='json',
        )

    def test_sequential_second_application_is_rejected(self):
        first = self._apply()
        self.assertEqual(first.status_code, 201)

        second = self._apply()
        self.assertEqual(second.status_code, 400)
        self.assertEqual(
            Loan.objects.filter(membership=self.membership).count(), 1,
        )

    def test_concurrent_applications_admit_exactly_one(self):
        if connection.vendor == 'sqlite':
            self.skipTest(
                'SQLite select_for_update is a no-op; run on PostgreSQL.'
            )

        barrier = threading.Barrier(2)
        results = []

        def worker(_):
            barrier.wait()
            try:
                results.append(self._apply().status_code)
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(worker, range(2)))

        self.assertEqual(sorted(results), [201, 400])
        loans = Loan.objects.filter(membership=self.membership)
        self.assertEqual(loans.count(), 1)
        self.assertLessEqual(
            sum((loan.amount for loan in loans), Decimal('0')),
            Decimal('10000.00'),
        )


class GuarantorCapacityConcurrencyTests(TransactionTestCase):
    """Two guarantee approvals for one guarantor cannot jointly over-commit."""

    def setUp(self):
        self.sacco = Sacco.objects.create(
            name='Capacity Race SACCO',
            registration_number='CPR001',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
            membership_type=Sacco.MembershipType.OPEN,
            loan_multiplier=Decimal('3.00'),
            min_loan_months=0,
        )
        self.guarantor, self.guarantor_membership = _approved_member(
            self.sacco, 'cap-race-g@example.com', 'CPR-G-001', '254712820001',
        )
        savings_type = SavingsType.objects.create(
            sacco=self.sacco,
            name=SavingsType.Name.BOSA,
            minimum_contribution=Decimal('100.00'),
        )
        Saving.objects.create(
            membership=self.guarantor_membership,
            savings_type=savings_type,
            amount=Decimal('20000.00'),
            status=Saving.Status.ACTIVE,
        )
        loan_type = LoanType.objects.create(
            sacco=self.sacco,
            name='Guaranteed Loan',
            interest_rate=Decimal('12.00'),
            max_term_months=36,
            min_amount=Decimal('100.00'),
            requires_guarantors=True,
            min_guarantors=1,
        )
        self.requests = []
        for idx in range(2):
            borrower, borrower_membership = _approved_member(
                self.sacco,
                f'cap-race-b{idx}@example.com',
                f'CPR-B-{idx:03d}',
                f'2547128300{idx:02d}',
            )
            loan = Loan.objects.create(
                membership=borrower_membership,
                loan_type=loan_type,
                amount=Decimal('10000.00'),
                interest_rate=Decimal('12.00'),
                term_months=12,
                outstanding_balance=Decimal('10000.00'),
                status=Loan.Status.GUARANTORS_PENDING,
            )
            self.requests.append(
                Guarantor.objects.create(
                    loan=loan,
                    guarantor=self.guarantor,
                    guarantee_amount=Decimal('7000.00'),
                    status=Guarantor.Status.PENDING,
                )
            )

        # Seed the capacity snapshot: 0.5 * 20000 = 10000 available.
        update_guarantee_capacity(self.guarantor)

    def _approve(self, request):
        client = APIClient()
        client.force_authenticate(user=self.guarantor)
        return client.post(
            reverse(
                'services:guarantor-respond',
                kwargs={
                    'loan_id': request.loan_id,
                    'guarantor_id': request.id,
                },
            ),
            {'action': 'APPROVE'},
            format='json',
        )

    def _approved_total(self):
        return Guarantor.objects.filter(
            guarantor=self.guarantor,
            status=Guarantor.Status.APPROVED,
        ).aggregate(total=Sum('guarantee_amount'))['total'] or Decimal('0')

    def test_sequential_second_approval_is_rejected(self):
        first = self._approve(self.requests[0])
        self.assertEqual(first.status_code, 200)

        second = self._approve(self.requests[1])
        self.assertEqual(second.status_code, 400)
        self.assertEqual(self._approved_total(), Decimal('7000.00'))

    def test_concurrent_approvals_admit_exactly_one(self):
        if connection.vendor == 'sqlite':
            self.skipTest(
                'SQLite select_for_update is a no-op; run on PostgreSQL.'
            )

        barrier = threading.Barrier(2)
        results = []

        def worker(request):
            barrier.wait()
            try:
                results.append(self._approve(request).status_code)
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(worker, self.requests))

        self.assertEqual(sorted(results), [200, 400])
        self.assertEqual(
            Guarantor.objects.filter(
                guarantor=self.guarantor,
                status=Guarantor.Status.APPROVED,
            ).count(),
            1,
        )
        self.assertLessEqual(self._approved_total(), Decimal('10000.00'))
