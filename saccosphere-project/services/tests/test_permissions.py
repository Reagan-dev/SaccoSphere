"""Direct permission tests for service-level permission classes."""

from decimal import Decimal
from types import SimpleNamespace

from django.test import TestCase
from django.utils import timezone

from accounts.models import Sacco, User
from saccomembership.models import Membership
from services.models import GuaranteeCapacity, Guarantor, Loan, LoanType
from services.permissions import GuarantorCapacityCheck


class ServicePermissionTestCase(TestCase):
    """Exercise every service permission class directly."""

    def setUp(self):
        """Create eligible guarantor capacity data."""
        self.guarantor_user = User.objects.create_user(
            email='service-permission-guarantor@example.com',
            first_name='Service',
            last_name='Guarantor',
            phone_number='254700000101',
            password='testpass123',
        )
        self.borrower = User.objects.create_user(
            email='service-permission-borrower@example.com',
            first_name='Service',
            last_name='Borrower',
            phone_number='254700000102',
            password='testpass123',
        )
        self.sacco = Sacco.objects.create(
            name='Service Permission SACCO',
            registration_number='SERVPERM001',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
            membership_type=Sacco.MembershipType.OPEN,
        )
        self.borrower_membership = Membership.objects.create(
            user=self.borrower,
            sacco=self.sacco,
            status=Membership.Status.APPROVED,
            member_number='SERV-PERM-B-001',
            approved_date=timezone.now(),
        )
        self.loan_type = LoanType.objects.create(
            sacco=self.sacco,
            name='Service Permission Loan',
            interest_rate=Decimal('12.00'),
            max_term_months=36,
            min_amount=Decimal('1000.00'),
            requires_guarantors=True,
            min_guarantors=1,
        )
        self.loan = Loan.objects.create(
            membership=self.borrower_membership,
            loan_type=self.loan_type,
            amount=Decimal('30000.00'),
            interest_rate=Decimal('12.00'),
            term_months=12,
            outstanding_balance=Decimal('30000.00'),
            status=Loan.Status.GUARANTORS_PENDING,
        )
        self.guarantee = Guarantor.objects.create(
            loan=self.loan,
            guarantor=self.guarantor_user,
            guarantee_amount=Decimal('10000.00'),
            status=Guarantor.Status.PENDING,
        )
        GuaranteeCapacity.objects.create(
            user=self.guarantor_user,
            total_savings=Decimal('50000.00'),
            active_guarantees=Decimal('0.00'),
            available_capacity=Decimal('50000.00'),
        )

    def test_guarantor_capacity_check_allows_valid_object_level(self):
        """Capacity permission allows a guarantor with enough capacity."""
        permission = GuarantorCapacityCheck()
        request = SimpleNamespace(user=self.guarantor_user)

        self.assertTrue(permission.has_permission(request, None))
        self.assertTrue(permission.has_object_permission(
            request,
            None,
            self.guarantee,
        ))
