"""Tests for saccomanagement.loan_utils helpers."""

from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from accounts.models import Sacco, User
from guarantor.models import ExternalGuarantor
from saccomanagement.loan_utils import build_guarantors_summary
from saccomembership.models import Membership
from services.models import Guarantor, Loan, LoanType


class BuildGuarantorsSummaryQueryCountTest(TestCase):
    """build_guarantors_summary must not scale with the number of loans."""

    def setUp(self):
        self.sacco = Sacco.objects.create(
            name='Summary SACCO',
            registration_number='SUM-001',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
        )
        self.loan_type = LoanType.objects.create(
            sacco=self.sacco,
            name='Summary Loan',
            interest_rate=Decimal('12.00'),
            max_term_months=24,
            min_amount=Decimal('1000.00'),
            requires_guarantors=True,
        )

    def _make_loan_with_guarantors(self, idx):
        borrower = User.objects.create_user(
            email=f'summary-borrower-{idx}@example.com',
            phone_number=f'2547000010{idx:02d}',
            password='testpass123',
        )
        membership = Membership.objects.create(
            user=borrower,
            sacco=self.sacco,
            status=Membership.Status.APPROVED,
            member_number=f'SUM-M-{idx:03d}',
            approved_date=timezone.now(),
        )
        loan = Loan.objects.create(
            membership=membership,
            loan_type=self.loan_type,
            amount=Decimal('40000.00'),
            interest_rate=Decimal('12.00'),
            term_months=12,
            outstanding_balance=Decimal('40000.00'),
            status=Loan.Status.BOARD_REVIEW,
        )
        internal_user = User.objects.create_user(
            email=f'summary-guar-{idx}@example.com',
            phone_number=f'2547000020{idx:02d}',
            password='testpass123',
        )
        Guarantor.objects.create(
            loan=loan,
            guarantor=internal_user,
            guarantee_amount=Decimal('15000.00'),
            status=Guarantor.Status.APPROVED,
        )
        ExternalGuarantor.objects.create(
            loan=loan,
            requested_by=borrower,
            sacco=self.sacco,
            full_name=f'External {idx}',
            phone_number='254700003000',
            id_number=f'1000000{idx}',
            employment_status=ExternalGuarantor.EmploymentStatus.EMPLOYED,
            monthly_income=Decimal('50000.00'),
            guarantee_amount=Decimal('25000.00'),
            status=ExternalGuarantor.Status.APPROVED_BY_ADMIN,
        )
        return loan

    def _run_summary_over_queue(self, loan_count):
        for idx in range(loan_count):
            self._make_loan_with_guarantors(idx)

        # 1 query for loans + 1 for the guarantors prefetch + 1 for the
        # external_guarantors prefetch. build_guarantors_summary must add
        # zero, so the total stays 3 no matter how many loans there are.
        with self.assertNumQueries(3):
            loans = list(
                Loan.objects.filter(status=Loan.Status.BOARD_REVIEW)
                .prefetch_related('guarantors', 'external_guarantors')
            )
            summaries = [build_guarantors_summary(loan) for loan in loans]
        return summaries

    def test_summary_is_correct(self):
        summaries = self._run_summary_over_queue(1)

        self.assertEqual(summaries[0]['internal_approved'], 1)
        self.assertEqual(summaries[0]['external_approved'], 1)
        self.assertEqual(summaries[0]['total_coverage'], '40000.00')

    def test_query_count_is_flat_as_loans_grow(self):
        # 7 loans, still exactly 3 queries (vs ~4 per loan with the old
        # .filter()/.count()/.aggregate() implementation). test_summary_
        # is_correct pins the N=1 case at the same 3.
        summaries = self._run_summary_over_queue(7)
        self.assertEqual(len(summaries), 7)
