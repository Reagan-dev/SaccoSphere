"""Direct permission tests for account-level permission classes."""

from decimal import Decimal
from types import SimpleNamespace

from django.test import TestCase
from django.utils import timezone

from accounts.models import KYCVerification, Sacco, User
from accounts.permissions import (
    IsEligibleGuarantor,
    IsKYCVerified,
    IsMemberOfSacco,
    IsOwnerOrSaccoAdmin,
    IsPhoneVerified,
    IsSaccoAdmin,
    IsSaccoAdminOrSuperAdmin,
    IsSuperAdmin,
)
from saccomanagement.models import Role
from saccomembership.models import Membership
from services.models import Loan, LoanType, Saving, SavingsType


class AccountPermissionTestCase(TestCase):
    """Exercise every account permission class directly."""

    def setUp(self):
        """Create eligible user, SACCO, membership, savings, and loan data."""
        self.user = User.objects.create_user(
            email='permission-user@example.com',
            first_name='Permission',
            last_name='User',
            phone_number='254700000001',
            password='testpass123',
        )
        self.borrower = User.objects.create_user(
            email='permission-borrower@example.com',
            first_name='Permission',
            last_name='Borrower',
            phone_number='254700000002',
            password='testpass123',
        )
        self.admin = User.objects.create_user(
            email='permission-admin@example.com',
            first_name='Permission',
            last_name='Admin',
            phone_number='254700000003',
            password='testpass123',
        )
        self.super_admin = User.objects.create_user(
            email='permission-super@example.com',
            first_name='Permission',
            last_name='Super',
            phone_number='254700000004',
            password='testpass123',
        )
        self.sacco = Sacco.objects.create(
            name='Permission SACCO',
            registration_number='PERM001',
            sector=Sacco.Sector.FINANCE,
            county='Nairobi',
            membership_type=Sacco.MembershipType.OPEN,
        )
        self.membership = Membership.objects.create(
            user=self.user,
            sacco=self.sacco,
            status=Membership.Status.APPROVED,
            member_number='PERM-M-001',
            approved_date=timezone.now(),
        )
        self.borrower_membership = Membership.objects.create(
            user=self.borrower,
            sacco=self.sacco,
            status=Membership.Status.APPROVED,
            member_number='PERM-B-001',
            approved_date=timezone.now(),
        )
        Role.objects.create(
            user=self.admin,
            sacco=self.sacco,
            name=Role.SACCO_ADMIN,
        )
        Role.objects.create(
            user=self.super_admin,
            name=Role.SUPER_ADMIN,
        )
        KYCVerification.objects.create(
            user=self.user,
            status=KYCVerification.Status.APPROVED,
        )
        self.savings_type = SavingsType.objects.create(
            sacco=self.sacco,
            name=SavingsType.Name.BOSA,
            minimum_contribution=Decimal('100.00'),
        )
        Saving.objects.create(
            membership=self.membership,
            savings_type=self.savings_type,
            amount=Decimal('50000.00'),
            status=Saving.Status.ACTIVE,
        )
        self.loan_type = LoanType.objects.create(
            sacco=self.sacco,
            name='Permission Loan',
            interest_rate=Decimal('12.00'),
            max_term_months=36,
            min_amount=Decimal('1000.00'),
            requires_guarantors=True,
            min_guarantors=1,
        )
        self.loan = Loan.objects.create(
            membership=self.borrower_membership,
            loan_type=self.loan_type,
            amount=Decimal('20000.00'),
            interest_rate=Decimal('12.00'),
            term_months=12,
            outstanding_balance=Decimal('20000.00'),
            status=Loan.Status.PENDING,
        )

    def _request_for(self, user):
        """Return the smallest request object needed by permissions."""
        return SimpleNamespace(user=user)

    def test_is_kyc_verified_allows_valid_user_at_object_level(self):
        """KYC permission can be instantiated and checked directly."""
        permission = IsKYCVerified()
        request = self._request_for(self.user)

        self.assertTrue(permission.has_permission(request, None))
        self.assertTrue(
            permission.has_object_permission(request, None, self.membership)
        )

    def test_is_phone_verified_allows_valid_user_at_object_level(self):
        """Phone permission can be instantiated and checked directly."""
        permission = IsPhoneVerified()
        request = self._request_for(self.user)

        self.assertTrue(permission.has_permission(request, None))
        self.assertTrue(
            permission.has_object_permission(request, None, self.membership)
        )

    def test_is_sacco_admin_allows_valid_admin_at_object_level(self):
        """SACCO admin permission allows an admin for the object's SACCO."""
        permission = IsSaccoAdmin()
        request = self._request_for(self.admin)

        self.assertTrue(permission.has_object_permission(
            request,
            None,
            self.loan_type,
        ))

    def test_is_super_admin_allows_valid_admin_at_object_level(self):
        """Super admin permission can be instantiated and checked directly."""
        permission = IsSuperAdmin()
        request = self._request_for(self.super_admin)

        self.assertTrue(permission.has_permission(request, None))
        self.assertTrue(
            permission.has_object_permission(request, None, self.membership)
        )

    def test_is_sacco_admin_or_super_admin_allows_admin_object_level(self):
        """Combined admin permission allows an admin for the object's SACCO."""
        permission = IsSaccoAdminOrSuperAdmin()
        request = self._request_for(self.admin)

        self.assertTrue(permission.has_object_permission(
            request,
            None,
            self.loan_type,
        ))

    def test_is_member_of_sacco_allows_active_member_object_level(self):
        """Membership permission allows an approved member of the SACCO."""
        permission = IsMemberOfSacco()
        request = self._request_for(self.user)

        self.assertTrue(permission.has_object_permission(
            request,
            None,
            self.loan_type,
        ))

    def test_is_owner_or_sacco_admin_allows_owner_object_level(self):
        """Owner/admin permission allows the owner of the object."""
        permission = IsOwnerOrSaccoAdmin()
        request = self._request_for(self.user)

        self.assertTrue(permission.has_object_permission(
            request,
            None,
            self.membership,
        ))

    def test_is_eligible_guarantor_allows_valid_guarantor_object_level(self):
        """Eligible guarantor check uses real services models."""
        permission = IsEligibleGuarantor()
        request = self._request_for(self.user)

        self.assertTrue(permission.has_object_permission(
            request,
            None,
            self.loan,
        ))
