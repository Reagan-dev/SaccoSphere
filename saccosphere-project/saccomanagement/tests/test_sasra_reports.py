"""Tests for SASRA regulatory return generation."""

from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from accounts.models import Sacco, User
from saccomanagement.models import Role
from saccomembership.models import Membership
from services.models import Loan, RepaymentSchedule, Saving, SavingsType
from saccomanagement.sasra_reports import (
    build_par_return,
    build_financial_position_return,
    build_membership_return,
    _classify_loan_by_days_overdue,
    PAR_CATEGORIES,
)


class PARClassificationTests(TestCase):
    """Test PAR bucket classification logic."""

    def test_classify_performing_loan(self):
        """Test loan with 0 days overdue is classified as PERFORMING."""
        category = _classify_loan_by_days_overdue(0)
        self.assertEqual(category, 'PERFORMING')

    def test_classify_watch_loan(self):
        """Test loan with 15 days overdue is classified as WATCH."""
        category = _classify_loan_by_days_overdue(15)
        self.assertEqual(category, 'WATCH')

    def test_classify_watch_boundary(self):
        """Test loan with 30 days overdue is classified as WATCH."""
        category = _classify_loan_by_days_overdue(30)
        self.assertEqual(category, 'WATCH')

    def test_classify_substandard_loan(self):
        """Test loan with 45 days overdue is classified as SUBSTANDARD."""
        category = _classify_loan_by_days_overdue(45)
        self.assertEqual(category, 'SUBSTANDARD')

    def test_classify_substandard_boundary(self):
        """Test loan with 90 days overdue is classified as SUBSTANDARD."""
        category = _classify_loan_by_days_overdue(90)
        self.assertEqual(category, 'SUBSTANDARD')

    def test_classify_doubtful_loan(self):
        """Test loan with 120 days overdue is classified as DOUBTFUL."""
        category = _classify_loan_by_days_overdue(120)
        self.assertEqual(category, 'DOUBTFUL')

    def test_classify_doubtful_boundary(self):
        """Test loan with 180 days overdue is classified as DOUBTFUL."""
        category = _classify_loan_by_days_overdue(180)
        self.assertEqual(category, 'DOUBTFUL')

    def test_classify_loss_loan(self):
        """Test loan with 200 days overdue is classified as LOSS."""
        category = _classify_loan_by_days_overdue(200)
        self.assertEqual(category, 'LOSS')


class ProvisioningMathTests(TestCase):
    """Test SASRA provisioning rate calculations."""

    def setUp(self):
        """Set up test data."""
        self.sacco = Sacco.objects.create(
            name='Test SACCO',
            registration_number='TEST001',
            email='test@sacco.co.ke',
            phone='+254700000000',
            sector=Sacco.Sector.OTHER,
            county='Nairobi',
        )
        self.user = User.objects.create_user(
            email='member@test.com',
            password='testpass123',
        )
        self.membership = Membership.objects.create(
            user=self.user,
            sacco=self.sacco,
            status=Membership.Status.APPROVED,
        )

    def test_performing_provisioning_rate(self):
        """Test PERFORMING category uses 1% provisioning rate."""
        rate = PAR_CATEGORIES['PERFORMING']['provision_rate']
        self.assertEqual(rate, Decimal('0.01'))

        balance = Decimal('100000')
        provision = (balance * rate).quantize(Decimal('0.01'))
        self.assertEqual(provision, Decimal('1000.00'))

    def test_watch_provisioning_rate(self):
        """Test WATCH category uses 3% provisioning rate."""
        rate = PAR_CATEGORIES['WATCH']['provision_rate']
        self.assertEqual(rate, Decimal('0.03'))

        balance = Decimal('100000')
        provision = (balance * rate).quantize(Decimal('0.01'))
        self.assertEqual(provision, Decimal('3000.00'))

    def test_substandard_provisioning_rate(self):
        """Test SUBSTANDARD category uses 20% provisioning rate."""
        rate = PAR_CATEGORIES['SUBSTANDARD']['provision_rate']
        self.assertEqual(rate, Decimal('0.20'))

        balance = Decimal('100000')
        provision = (balance * rate).quantize(Decimal('0.01'))
        self.assertEqual(provision, Decimal('20000.00'))

    def test_doubtful_provisioning_rate(self):
        """Test DOUBTFUL category uses 50% provisioning rate."""
        rate = PAR_CATEGORIES['DOUBTFUL']['provision_rate']
        self.assertEqual(rate, Decimal('0.50'))

        balance = Decimal('100000')
        provision = (balance * rate).quantize(Decimal('0.01'))
        self.assertEqual(provision, Decimal('50000.00'))

    def test_loss_provisioning_rate(self):
        """Test LOSS category uses 100% provisioning rate."""
        rate = PAR_CATEGORIES['LOSS']['provision_rate']
        self.assertEqual(rate, Decimal('1.00'))

        balance = Decimal('100000')
        provision = (balance * rate).quantize(Decimal('0.01'))
        self.assertEqual(provision, Decimal('100000.00'))

    def test_par_return_with_active_loans(self):
        """Test PAR return generation with active loans."""
        # Create loan types
        from services.models import LoanType
        loan_type = LoanType.objects.create(
            sacco=self.sacco,
            name='Personal Loan',
            interest_rate=Decimal('12.00'),
            max_term_months=12,
            min_amount=Decimal('1000'),
        )

        # Create active loan
        loan = Loan.objects.create(
            membership=self.membership,
            loan_type=loan_type,
            amount=Decimal('50000'),
            interest_rate=Decimal('12.00'),
            term_months=12,
            outstanding_balance=Decimal('45000'),
            status=Loan.Status.ACTIVE,
            disbursement_date=timezone.localdate() - timedelta(days=60),
        )

        # Create repayment schedule with overdue instalment
        due_date = timezone.localdate() - timedelta(days=45)
        RepaymentSchedule.objects.create(
            loan=loan,
            instalment_number=1,
            due_date=due_date,
            amount=Decimal('5000'),
            principal=Decimal('4000'),
            interest=Decimal('1000'),
            balance_after=Decimal('41000'),
            status=RepaymentSchedule.Status.PENDING,
        )

        # Generate PAR return
        as_of_date = timezone.localdate()
        result = build_par_return(self.sacco, as_of_date)

        # Verify structure
        self.assertIn('as_of_date', result)
        self.assertIn('total_outstanding_book', result)
        self.assertIn('categories', result)
        self.assertIn('par30_ratio', result)
        self.assertIn('par90_ratio', result)

        # Verify loan classification (45 days overdue = SUBSTANDARD)
        self.assertEqual(
            result['categories']['SUBSTANDARD']['loan_count'],
            1
        )
        self.assertEqual(
            result['categories']['SUBSTANDARD']['outstanding_balance'],
            '45000.00'
        )

        # Verify provision calculation (20% of 45000 = 9000)
        self.assertEqual(
            result['categories']['SUBSTANDARD']['provision_required'],
            '9000.00'
        )

    def test_par_return_with_performing_loan(self):
        """Test PAR return with performing loan (no overdue instalments)."""
        from services.models import LoanType
        loan_type = LoanType.objects.create(
            sacco=self.sacco,
            name='Personal Loan',
            interest_rate=Decimal('12.00'),
            max_term_months=12,
            min_amount=Decimal('1000'),
        )

        # Create active loan with no overdue instalments
        loan = Loan.objects.create(
            membership=self.membership,
            loan_type=loan_type,
            amount=Decimal('50000'),
            interest_rate=Decimal('12.00'),
            term_months=12,
            outstanding_balance=Decimal('50000'),
            status=Loan.Status.ACTIVE,
            disbursement_date=timezone.localdate() - timedelta(days=10),
        )

        # Create future instalment
        due_date = timezone.localdate() + timedelta(days=30)
        RepaymentSchedule.objects.create(
            loan=loan,
            instalment_number=1,
            due_date=due_date,
            amount=Decimal('5000'),
            principal=Decimal('4000'),
            interest=Decimal('1000'),
            balance_after=Decimal('46000'),
            status=RepaymentSchedule.Status.PENDING,
        )

        # Generate PAR return
        as_of_date = timezone.localdate()
        result = build_par_return(self.sacco, as_of_date)

        # Verify loan is classified as PERFORMING
        self.assertEqual(
            result['categories']['PERFORMING']['loan_count'],
            1
        )
        self.assertEqual(
            result['categories']['PERFORMING']['outstanding_balance'],
            '50000.00'
        )

        # Verify provision calculation (1% of 50000 = 500)
        self.assertEqual(
            result['categories']['PERFORMING']['provision_required'],
            '500.00'
        )


class FinancialPositionTests(TestCase):
    """Test financial position return generation."""

    def setUp(self):
        """Set up test data."""
        self.sacco = Sacco.objects.create(
            name='Test SACCO',
            registration_number='TEST001',
            email='test@sacco.co.ke',
            phone='+254700000000',
            sector=Sacco.Sector.OTHER,
            county='Nairobi',
        )
        self.user = User.objects.create_user(
            email='member@test.com',
            password='testpass123',
        )
        self.membership = Membership.objects.create(
            user=self.user,
            sacco=self.sacco,
            status=Membership.Status.APPROVED,
        )

    def test_financial_position_structure(self):
        """Test financial position return has correct structure."""
        as_of_date = timezone.localdate()
        result = build_financial_position_return(self.sacco, as_of_date)

        self.assertIn('as_of_date', result)
        self.assertIn('assets', result)
        self.assertIn('liabilities', result)
        self.assertIn('loans_outstanding', result['assets'])
        self.assertIn('cash_balance', result['assets'])
        self.assertIn('total_assets', result['assets'])
        self.assertIn('savings_by_type', result['liabilities'])
        self.assertIn('total_liabilities', result['liabilities'])

    def test_savings_by_type_breakdown(self):
        """Test savings are broken down by type."""
        # Create savings types
        bosa_type = SavingsType.objects.create(
            sacco=self.sacco,
            name=SavingsType.Name.BOSA,
        )
        fosa_type = SavingsType.objects.create(
            sacco=self.sacco,
            name=SavingsType.Name.FOSA,
        )
        share_type = SavingsType.objects.create(
            sacco=self.sacco,
            name=SavingsType.Name.SHARE_CAPITAL,
        )

        # Create savings accounts
        Saving.objects.create(
            membership=self.membership,
            savings_type=bosa_type,
            amount=Decimal('50000'),
            status=Saving.Status.ACTIVE,
        )
        Saving.objects.create(
            membership=self.membership,
            savings_type=fosa_type,
            amount=Decimal('20000'),
            status=Saving.Status.ACTIVE,
        )
        Saving.objects.create(
            membership=self.membership,
            savings_type=share_type,
            amount=Decimal('10000'),
            status=Saving.Status.ACTIVE,
        )

        # Generate financial position
        as_of_date = timezone.localdate()
        result = build_financial_position_return(self.sacco, as_of_date)

        # Verify breakdown
        self.assertEqual(
            result['liabilities']['savings_by_type']['BOSA'],
            '50000.00'
        )
        self.assertEqual(
            result['liabilities']['savings_by_type']['FOSA'],
            '20000.00'
        )
        self.assertEqual(
            result['liabilities']['savings_by_type']['SHARE_CAPITAL'],
            '10000.00'
        )
        self.assertEqual(
            result['liabilities']['total_liabilities'],
            '80000.00'
        )


class MembershipReturnTests(TestCase):
    """Test membership return generation."""

    def setUp(self):
        """Set up test data."""
        self.sacco = Sacco.objects.create(
            name='Test SACCO',
            registration_number='TEST001',
            email='test@sacco.co.ke',
            phone='+254700000000',
            sector=Sacco.Sector.OTHER,
            county='Nairobi',
        )

    def test_membership_structure(self):
        """Test membership return has correct structure."""
        period_start = date(2024, 1, 1)
        period_end = date(2024, 1, 31)
        result = build_membership_return(self.sacco, period_start, period_end)

        self.assertIn('period_start', result)
        self.assertIn('period_end', result)
        self.assertIn('current_members_by_status', result)
        self.assertIn('total_current_members', result)
        self.assertIn('new_registrations', result)
        self.assertIn('exits', result)

    def test_membership_counts_by_status(self):
        """Test membership counts by status."""
        user1 = User.objects.create_user(
            email='member1@test.com',
            password='testpass123',
        )
        user2 = User.objects.create_user(
            email='member2@test.com',
            password='testpass123',
        )
        user3 = User.objects.create_user(
            email='member3@test.com',
            password='testpass123',
        )

        Membership.objects.create(
            user=user1,
            sacco=self.sacco,
            status=Membership.Status.APPROVED,
        )
        Membership.objects.create(
            user=user2,
            sacco=self.sacco,
            status=Membership.Status.PENDING,
        )
        Membership.objects.create(
            user=user3,
            sacco=self.sacco,
            status=Membership.Status.LEFT,
        )

        period_start = date(2024, 1, 1)
        period_end = date(2024, 1, 31)
        result = build_membership_return(self.sacco, period_start, period_end)

        self.assertEqual(result['current_members_by_status']['APPROVED'], 1)
        self.assertEqual(result['current_members_by_status']['PENDING'], 1)
        self.assertEqual(result['current_members_by_status']['LEFT'], 1)
        self.assertEqual(result['total_current_members'], 3)

    def test_new_registrations_in_period(self):
        """Test new registrations count for period."""
        user = User.objects.create_user(
            email='member@test.com',
            password='testpass123',
        )

        # Create membership approved during period
        Membership.objects.create(
            user=user,
            sacco=self.sacco,
            status=Membership.Status.APPROVED,
            approved_date=date(2024, 1, 15),
        )

        period_start = date(2024, 1, 1)
        period_end = date(2024, 1, 31)
        result = build_membership_return(self.sacco, period_start, period_end)

        self.assertEqual(result['new_registrations'], 1)

    def test_exits_in_period(self):
        """Test exits count for period."""
        user = User.objects.create_user(
            email='member@test.com',
            password='testpass123',
        )

        # Create membership that left during period
        membership = Membership.objects.create(
            user=user,
            sacco=self.sacco,
            status=Membership.Status.LEFT,
        )
        # Update the updated_at to be within period using direct update (bypasses auto_now)
        from django.utils import timezone
        exit_datetime = timezone.make_aware(
            timezone.datetime(2024, 1, 20, 12, 0, 0)
        )
        Membership.objects.filter(id=membership.id).update(updated_at=exit_datetime)

        period_start = date(2024, 1, 1)
        period_end = date(2024, 1, 31)
        result = build_membership_return(self.sacco, period_start, period_end)

        self.assertEqual(result['exits'], 1)


class XLSXExportTests(TestCase):
    """Test XLSX export functionality."""

    def setUp(self):
        """Set up test data."""
        self.sacco = Sacco.objects.create(
            name='Test SACCO',
            registration_number='TEST001',
            email='test@sacco.co.ke',
            phone='+254700000000',
            sector=Sacco.Sector.OTHER,
            county='Nairobi',
        )
        self.user = User.objects.create_user(
            email='admin@test.com',
            password='testpass123',
        )
        self.membership = Membership.objects.create(
            user=self.user,
            sacco=self.sacco,
            status=Membership.Status.APPROVED,
        )
        # Create admin role
        Role.objects.create(
            user=self.user,
            sacco=self.sacco,
            name=Role.SACCO_ADMIN,
        )

    def test_par_xlsx_export(self):
        """Test PAR return XLSX export."""
        from saccomanagement.sasra_reports import SASRAReturnView
        from rest_framework.test import APIRequestFactory

        view = SASRAReturnView()
        factory = APIRequestFactory()

        # Create test data
        from services.models import LoanType
        loan_type = LoanType.objects.create(
            sacco=self.sacco,
            name='Personal Loan',
            interest_rate=Decimal('12.00'),
            max_term_months=12,
            min_amount=Decimal('1000'),
        )
        loan = Loan.objects.create(
            membership=self.membership,
            loan_type=loan_type,
            amount=Decimal('50000'),
            interest_rate=Decimal('12.00'),
            term_months=12,
            outstanding_balance=Decimal('45000'),
            status=Loan.Status.ACTIVE,
        )

        # Generate report data
        as_of_date = timezone.localdate()
        report_data = build_par_return(self.sacco, as_of_date)

        # Generate XLSX
        response = view._generate_xlsx(
            report_data,
            'par',
            self.sacco,
            as_of_date
        )

        # Verify response
        self.assertEqual(
            response['Content-Type'],
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        self.assertIn('attachment', response['Content-Disposition'])
        self.assertIn('.xlsx', response['Content-Disposition'])

    def test_financial_position_xlsx_export(self):
        """Test financial position XLSX export."""
        from saccomanagement.sasra_reports import SASRAReturnView

        view = SASRAReturnView()

        as_of_date = timezone.localdate()
        report_data = build_financial_position_return(self.sacco, as_of_date)

        response = view._generate_xlsx(
            report_data,
            'financial_position',
            self.sacco,
            as_of_date
        )

        self.assertEqual(
            response['Content-Type'],
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )

    def test_membership_xlsx_export(self):
        """Test membership XLSX export."""
        from saccomanagement.sasra_reports import SASRAReturnView

        view = SASRAReturnView()

        period_start = date(2024, 1, 1)
        period_end = date(2024, 1, 31)
        report_data = build_membership_return(
            self.sacco,
            period_start,
            period_end
        )

        response = view._generate_xlsx(
            report_data,
            'membership',
            self.sacco,
            period_end
        )

        self.assertEqual(
            response['Content-Type'],
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
