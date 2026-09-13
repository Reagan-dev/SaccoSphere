"""Tests for the member dashboard: portfolio, activity feed, loan compare."""

from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models import Sacco, User
from dashboard.engines.activity_feed import get_activity_feed
from dashboard.engines.loan_comparator import compare_loan_options
from dashboard.engines.portfolio_builder import (
    get_dashboard_state,
    get_sacco_switcher_data,
    get_unified_portfolio,
)
from notifications.models import Notification
from payments.models import Transaction
from saccomembership.models import Membership
from services.models import Loan, LoanType, RepaymentSchedule, Saving, SavingsType


class PortfolioBuilderTestCase(TestCase):
    """Test cross-SACCO portfolio aggregation for a member."""

    def setUp(self):
        self.user = User.objects.create_user(
            email='portfolio@example.com',
            password='StrongPass123',
            first_name='Portfolio',
            last_name='Member',
        )
        self.sacco_a = Sacco.objects.create(
            name='Alpha SACCO',
            registration_number='ALPHA001',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
        )
        self.sacco_b = Sacco.objects.create(
            name='Beta SACCO',
            registration_number='BETA001',
            sector=Sacco.Sector.TECHNOLOGY,
            county='Kiambu',
        )

    def test_aggregates_savings_and_loans_across_saccos(self):
        """Totals sum active savings and loans from every approved SACCO."""
        membership_a = Membership.objects.create(
            user=self.user,
            sacco=self.sacco_a,
            status=Membership.Status.APPROVED,
            member_number='ALPHA-M001',
        )
        membership_b = Membership.objects.create(
            user=self.user,
            sacco=self.sacco_b,
            status=Membership.Status.APPROVED,
            member_number='BETA-M001',
        )
        bosa_type = SavingsType.objects.create(
            sacco=self.sacco_a,
            name=SavingsType.Name.BOSA,
            minimum_contribution=Decimal('500.00'),
        )
        Saving.objects.create(
            membership=membership_a,
            savings_type=bosa_type,
            amount=Decimal('2000.00'),
            status=Saving.Status.ACTIVE,
        )
        other_type = SavingsType.objects.create(
            sacco=self.sacco_b,
            name=SavingsType.Name.BOSA,
            minimum_contribution=Decimal('500.00'),
        )
        Saving.objects.create(
            membership=membership_b,
            savings_type=other_type,
            amount=Decimal('1500.00'),
            status=Saving.Status.ACTIVE,
        )
        Loan.objects.create(
            membership=membership_a,
            amount=Decimal('5000.00'),
            interest_rate=Decimal('12.00'),
            term_months=6,
            outstanding_balance=Decimal('3000.00'),
            status=Loan.Status.ACTIVE,
        )

        portfolio = get_unified_portfolio(self.user)

        self.assertEqual(portfolio['total_saccos'], 2)
        self.assertEqual(portfolio['total_savings'], Decimal('3500.00'))
        self.assertEqual(portfolio['total_active_loans'], 1)

    def test_excludes_pending_membership(self):
        """A pending (unapproved) membership is not part of the portfolio."""
        Membership.objects.create(
            user=self.user,
            sacco=self.sacco_a,
            status=Membership.Status.PENDING,
            member_number='ALPHA-M002',
        )

        portfolio = get_unified_portfolio(self.user)

        self.assertEqual(portfolio['total_saccos'], 0)
        self.assertEqual(portfolio['saccos'], [])

    def test_excludes_other_users_data(self):
        """Another member's SACCO data must never appear in this portfolio."""
        other_user = User.objects.create_user(
            email='other-portfolio@example.com',
            password='StrongPass123',
            first_name='Other',
            last_name='Member',
        )
        Membership.objects.create(
            user=other_user,
            sacco=self.sacco_a,
            status=Membership.Status.APPROVED,
            member_number='ALPHA-M003',
        )
        Membership.objects.create(
            user=self.user,
            sacco=self.sacco_b,
            status=Membership.Status.APPROVED,
            member_number='BETA-M002',
        )

        portfolio = get_unified_portfolio(self.user)

        self.assertEqual(portfolio['total_saccos'], 1)
        self.assertEqual(
            portfolio['saccos'][0]['sacco_id'],
            str(self.sacco_b.id),
        )

    def test_frozen_savings_excluded_from_total(self):
        """Only ACTIVE savings count toward the portfolio total."""
        membership = Membership.objects.create(
            user=self.user,
            sacco=self.sacco_a,
            status=Membership.Status.APPROVED,
            member_number='ALPHA-M004',
        )
        savings_type = SavingsType.objects.create(
            sacco=self.sacco_a,
            name=SavingsType.Name.BOSA,
            minimum_contribution=Decimal('500.00'),
        )
        Saving.objects.create(
            membership=membership,
            savings_type=savings_type,
            amount=Decimal('1000.00'),
            status=Saving.Status.ACTIVE,
        )
        Saving.objects.create(
            membership=membership,
            savings_type=savings_type,
            amount=Decimal('5000.00'),
            status=Saving.Status.FROZEN,
        )

        portfolio = get_unified_portfolio(self.user)

        self.assertEqual(portfolio['total_savings'], Decimal('1000.00'))


class DashboardStateTestCase(TestCase):
    """Test the coarse-grained onboarding state machine."""

    def setUp(self):
        self.user = User.objects.create_user(
            email='state@example.com',
            password='StrongPass123',
            first_name='State',
            last_name='Member',
        )
        self.sacco = Sacco.objects.create(
            name='Alpha SACCO',
            registration_number='ALPHA001',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
        )

    def test_no_saccos_when_no_memberships(self):
        state = get_dashboard_state(self.user)
        self.assertEqual(state['state'], 'NO_SACCOS')

    def test_fully_active_when_all_approved(self):
        Membership.objects.create(
            user=self.user,
            sacco=self.sacco,
            status=Membership.Status.APPROVED,
            member_number='ALPHA-M001',
        )
        state = get_dashboard_state(self.user)
        self.assertEqual(state['state'], 'FULLY_ACTIVE')

    def test_partial_active_when_mixed_with_pending(self):
        other_sacco = Sacco.objects.create(
            name='Beta SACCO',
            registration_number='BETA001',
            sector=Sacco.Sector.TECHNOLOGY,
            county='Kiambu',
        )
        Membership.objects.create(
            user=self.user,
            sacco=self.sacco,
            status=Membership.Status.APPROVED,
            member_number='ALPHA-M002',
        )
        Membership.objects.create(
            user=self.user,
            sacco=other_sacco,
            status=Membership.Status.PENDING,
            member_number='BETA-M002',
        )
        state = get_dashboard_state(self.user)
        self.assertEqual(state['state'], 'PARTIAL_ACTIVE')

    def test_all_pending_when_only_pending(self):
        Membership.objects.create(
            user=self.user,
            sacco=self.sacco,
            status=Membership.Status.PENDING,
            member_number='ALPHA-M003',
        )
        state = get_dashboard_state(self.user)
        self.assertEqual(state['state'], 'ALL_PENDING')

    def test_suspended_when_only_suspended(self):
        Membership.objects.create(
            user=self.user,
            sacco=self.sacco,
            status=Membership.Status.SUSPENDED,
            member_number='ALPHA-M004',
        )
        state = get_dashboard_state(self.user)
        self.assertEqual(state['state'], 'SUSPENDED')


class SaccoSwitcherTestCase(TestCase):
    """Test the per-SACCO switcher card data."""

    def setUp(self):
        self.user = User.objects.create_user(
            email='switcher@example.com',
            password='StrongPass123',
            first_name='Switcher',
            last_name='Member',
        )
        self.sacco = Sacco.objects.create(
            name='Alpha SACCO',
            registration_number='ALPHA001',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
        )

    def test_switcher_card_scopes_totals_to_its_own_sacco(self):
        membership = Membership.objects.create(
            user=self.user,
            sacco=self.sacco,
            status=Membership.Status.APPROVED,
            member_number='ALPHA-M001',
        )
        savings_type = SavingsType.objects.create(
            sacco=self.sacco,
            name=SavingsType.Name.BOSA,
            minimum_contribution=Decimal('500.00'),
        )
        Saving.objects.create(
            membership=membership,
            savings_type=savings_type,
            amount=Decimal('1200.00'),
            status=Saving.Status.ACTIVE,
        )
        Loan.objects.create(
            membership=membership,
            amount=Decimal('4000.00'),
            interest_rate=Decimal('12.00'),
            term_months=6,
            outstanding_balance=Decimal('4000.00'),
            status=Loan.Status.ACTIVE,
        )
        Notification.objects.create(
            user=self.user,
            title='Unrelated alert',
            message='Not tied to this SACCO.',
            category=Notification.Category.SYSTEM,
            is_read=False,
        )

        cards = get_sacco_switcher_data(self.user)

        self.assertEqual(len(cards), 1)
        card = cards[0]
        self.assertEqual(card['savings_total'], Decimal('1200.00'))
        self.assertEqual(card['active_loans'], 1)
        self.assertEqual(card['unread_notifications'], 0)


class ActivityFeedTestCase(TestCase):
    """Test the merged payment/repayment activity feed."""

    def setUp(self):
        self.user = User.objects.create_user(
            email='activity@example.com',
            password='StrongPass123',
            first_name='Activity',
            last_name='Member',
        )
        self.sacco = Sacco.objects.create(
            name='Alpha SACCO',
            registration_number='ALPHA001',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
        )
        self.membership = Membership.objects.create(
            user=self.user,
            sacco=self.sacco,
            status=Membership.Status.APPROVED,
            member_number='ALPHA-M001',
        )

    def test_merges_completed_payments_and_paid_instalments(self):
        Transaction.objects.create(
            user=self.user,
            sacco=self.sacco,
            reference='TXN-ACTIVITY-001',
            transaction_type=Transaction.TransactionType.DEPOSIT,
            amount=Decimal('500.00'),
            status=Transaction.Status.COMPLETED,
        )
        loan_type = LoanType.objects.create(
            sacco=self.sacco,
            name='Emergency Loan',
            interest_rate=Decimal('12.00'),
            max_term_months=12,
            min_amount=Decimal('1000.00'),
        )
        loan = Loan.objects.create(
            membership=self.membership,
            loan_type=loan_type,
            amount=Decimal('3000.00'),
            interest_rate=Decimal('12.00'),
            term_months=3,
            outstanding_balance=Decimal('2000.00'),
            status=Loan.Status.ACTIVE,
        )
        RepaymentSchedule.objects.create(
            loan=loan,
            instalment_number=1,
            due_date=timezone.localdate(),
            amount=Decimal('1000.00'),
            principal=Decimal('900.00'),
            interest=Decimal('100.00'),
            balance_after=Decimal('2000.00'),
            status=RepaymentSchedule.Status.PAID,
            paid_date=timezone.localdate(),
            paid_amount=Decimal('1000.00'),
        )

        activity = get_activity_feed(self.user, limit=20)

        types = {item['type'] for item in activity}
        self.assertEqual(types, {'PAYMENT', 'REPAYMENT'})
        self.assertEqual(len(activity), 2)

    def test_excludes_other_users_activity(self):
        other_user = User.objects.create_user(
            email='other-activity@example.com',
            password='StrongPass123',
            first_name='Other',
            last_name='Member',
        )
        Transaction.objects.create(
            user=other_user,
            sacco=self.sacco,
            reference='TXN-ACTIVITY-002',
            transaction_type=Transaction.TransactionType.DEPOSIT,
            amount=Decimal('500.00'),
            status=Transaction.Status.COMPLETED,
        )

        activity = get_activity_feed(self.user, limit=20)

        self.assertEqual(activity, [])

    def test_respects_limit(self):
        for index in range(5):
            Transaction.objects.create(
                user=self.user,
                sacco=self.sacco,
                reference=f'TXN-ACTIVITY-LIMIT-{index}',
                transaction_type=Transaction.TransactionType.DEPOSIT,
                amount=Decimal('100.00'),
                status=Transaction.Status.COMPLETED,
            )

        activity = get_activity_feed(self.user, limit=3)

        self.assertEqual(len(activity), 3)


class LoanComparatorTestCase(TestCase):
    """Test cross-SACCO loan product comparison."""

    def setUp(self):
        self.user = User.objects.create_user(
            email='comparator@example.com',
            password='StrongPass123',
            first_name='Comparator',
            last_name='Member',
        )
        self.sacco = Sacco.objects.create(
            name='Alpha SACCO',
            registration_number='ALPHA001',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
        )
        Membership.objects.create(
            user=self.user,
            sacco=self.sacco,
            status=Membership.Status.APPROVED,
            member_number='ALPHA-M001',
        )

    def test_filters_by_amount_and_term_bounds(self):
        LoanType.objects.create(
            sacco=self.sacco,
            name='Emergency Loan',
            interest_rate=Decimal('12.00'),
            max_term_months=6,
            min_amount=Decimal('1000.00'),
            max_amount=Decimal('5000.00'),
        )
        LoanType.objects.create(
            sacco=self.sacco,
            name='Development Loan',
            interest_rate=Decimal('10.00'),
            max_term_months=24,
            min_amount=Decimal('10000.00'),
        )

        options = compare_loan_options(
            self.user,
            requested_amount=Decimal('3000.00'),
            term_months=6,
        )

        self.assertEqual(len(options), 1)
        self.assertEqual(options[0]['loan_type_name'], 'Emergency Loan')

    def test_excludes_inactive_loan_types(self):
        LoanType.objects.create(
            sacco=self.sacco,
            name='Retired Loan',
            interest_rate=Decimal('12.00'),
            max_term_months=12,
            min_amount=Decimal('1000.00'),
            is_active=False,
        )

        options = compare_loan_options(
            self.user,
            requested_amount=Decimal('2000.00'),
            term_months=6,
        )

        self.assertEqual(options, [])

    def test_excludes_saccos_the_user_does_not_belong_to(self):
        other_sacco = Sacco.objects.create(
            name='Beta SACCO',
            registration_number='BETA001',
            sector=Sacco.Sector.TECHNOLOGY,
            county='Kiambu',
        )
        LoanType.objects.create(
            sacco=other_sacco,
            name='Beta Loan',
            interest_rate=Decimal('12.00'),
            max_term_months=12,
            min_amount=Decimal('1000.00'),
        )

        options = compare_loan_options(
            self.user,
            requested_amount=Decimal('2000.00'),
            term_months=6,
        )

        self.assertEqual(options, [])


class DashboardViewsAuthTestCase(TestCase):
    """Smoke-test authentication and wiring for the dashboard endpoints."""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            email='views@example.com',
            password='StrongPass123',
            first_name='Views',
            last_name='Member',
        )

    def test_endpoints_require_authentication(self):
        endpoints = (
            '/api/v1/dashboard/portfolio/',
            '/api/v1/dashboard/state/',
            '/api/v1/dashboard/saccos/',
            '/api/v1/dashboard/activity/',
            '/api/v1/dashboard/loans/compare/?amount=1000&term=6',
        )
        for endpoint in endpoints:
            with self.subTest(endpoint=endpoint):
                response = self.client.get(endpoint)
                self.assertEqual(
                    response.status_code,
                    status.HTTP_401_UNAUTHORIZED,
                )

    def test_portfolio_view_returns_empty_state_for_new_member(self):
        self.client.force_authenticate(user=self.user)

        response = self.client.get('/api/v1/dashboard/portfolio/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['total_saccos'], 0)

    def test_loan_comparison_requires_amount_and_term(self):
        self.client.force_authenticate(user=self.user)

        response = self.client.get('/api/v1/dashboard/loans/compare/')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('amount', response.data['errors'])

    def test_loan_comparison_rejects_non_positive_amount(self):
        self.client.force_authenticate(user=self.user)

        response = self.client.get(
            '/api/v1/dashboard/loans/compare/?amount=0&term=6',
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('amount', response.data['errors'])
