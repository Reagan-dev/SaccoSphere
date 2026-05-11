from decimal import Decimal

from rest_framework.permissions import BasePermission

from saccomanagement.models import Role


class IsKYCVerified(BasePermission):
    """
    Allow access only when the authenticated user's KYC is approved.
    """

    message = 'You must complete KYC verification first.'

    def has_permission(self, request, view):
        """Check if user's KYC status is APPROVED."""
        user = request.user

        if not user or not user.is_authenticated:
            return False

        return (
            getattr(getattr(user, 'kyc', None), 'status', None)
            == 'APPROVED'
        )


class IsPhoneVerified(BasePermission):
    """
    Allow access only when the authenticated user has a phone number.
    """

    message = 'You must verify your phone number first.'

    def has_permission(self, request, view):
        """Check if user has a phone number."""
        user = request.user

        if not user or not user.is_authenticated:
            return False

        return bool(user.phone_number)


class IsSaccoAdmin(BasePermission):
    """
    Allow access only to users with SACCO_ADMIN role.

    At view level: Requires SACCO_ADMIN role at any SACCO.
    At object level: Requires SACCO_ADMIN role for the object's SACCO.
    """

    message = 'You must be a SACCO admin to perform this action.'

    def has_permission(self, request, view):
        """Check if user is authenticated and has SACCO_ADMIN role."""
        user = request.user

        if not user or not user.is_authenticated:
            return False

        return user.roles.filter(name=Role.SACCO_ADMIN).exists()

    def has_object_permission(self, request, view, obj):
        """
        Check if user has SACCO_ADMIN role for the object's SACCO.

        Object must have a .sacco attribute.
        """
        if not hasattr(obj, 'sacco'):
            return False

        user = request.user

        if not user or not user.is_authenticated:
            return False

        return user.roles.filter(
            name=Role.SACCO_ADMIN,
            sacco=obj.sacco,
        ).exists()


class IsSuperAdmin(BasePermission):
    """
    Allow access only to super admins.

    Super admins are users marked as staff or who have SUPER_ADMIN role.
    """

    message = 'You must be a super admin to perform this action.'

    def has_permission(self, request, view):
        """Check if user is staff or has SUPER_ADMIN role."""
        user = request.user

        if not user or not user.is_authenticated:
            return False

        return (
            user.is_staff
            or user.roles.filter(name=Role.SUPER_ADMIN).exists()
        )


class IsSaccoAdminOrSuperAdmin(BasePermission):
    """
    Allow access to users with SACCO_ADMIN or SUPER_ADMIN role.

    This is the combination permission for resource-level access.
    At object level: SUPER_ADMIN bypasses sacco check; SACCO_ADMIN must have
    role for obj.sacco.
    """

    message = 'You must be a SACCO admin or super admin.'

    def has_permission(self, request, view):
        """Check if user has SACCO_ADMIN or SUPER_ADMIN role."""
        user = request.user

        if not user or not user.is_authenticated:
            return False

        return user.roles.filter(
            name__in=[Role.SACCO_ADMIN, Role.SUPER_ADMIN]
        ).exists()

    def has_object_permission(self, request, view, obj):
        """
        Check if user has SACCO_ADMIN or SUPER_ADMIN role.

        For SACCO_ADMIN: user must have role for obj.sacco.
        For SUPER_ADMIN: no sacco context required.
        """
        user = request.user

        if not user or not user.is_authenticated:
            return False

        # SUPER_ADMIN always passes at object level
        if user.roles.filter(name=Role.SUPER_ADMIN).exists():
            return True

        # SACCO_ADMIN must have role for the object's SACCO
        if hasattr(obj, 'sacco'):
            return user.roles.filter(
                name=Role.SACCO_ADMIN,
                sacco=obj.sacco,
            ).exists()

        if hasattr(obj, 'user'):
            from saccomembership.models import Membership

            admin_sacco_ids = user.roles.filter(
                name=Role.SACCO_ADMIN,
                sacco__isnull=False,
            ).values_list('sacco_id', flat=True)

            return Membership.objects.filter(
                user=obj.user,
                sacco_id__in=admin_sacco_ids,
            ).exists()

        return False


class IsMemberOfSacco(BasePermission):
    """
    Allow access only to active members of the SACCO.

    Checks that user has an APPROVED membership in the SACCO.
    """

    message = 'You must be an active member of this SACCO.'

    def has_object_permission(self, request, view, obj):
        """
        Check if user is an approved member of the object's SACCO.

        Object must have .sacco or .membership.sacco attribute.
        """
        user = request.user

        if not user or not user.is_authenticated:
            return False

        # Import here to avoid circular imports
        from saccomembership.models import Membership

        # Determine the sacco from obj
        sacco = None
        if hasattr(obj, 'sacco'):
            sacco = obj.sacco
        elif hasattr(obj, 'membership') and hasattr(
            obj.membership, 'sacco'
        ):
            sacco = obj.membership.sacco
        else:
            return False

        if not sacco:
            return False

        # Check if user has approved membership
        return Membership.objects.filter(
            user=user,
            sacco=sacco,
            status='APPROVED',
        ).exists()


class IsOwnerOrSaccoAdmin(BasePermission):
    """
    Allow access if user owns the resource or is the SACCO admin.

    Combines ownership check with SACCO admin privilege escalation.
    """

    message = 'You must own this resource or be the SACCO admin.'

    def has_object_permission(self, request, view, obj):
        """
        Check if user owns the object or is SACCO admin for its SACCO.

        Object must have .user and .sacco attributes.
        """
        user = request.user

        if not user or not user.is_authenticated:
            return False

        # Owner always has access
        if hasattr(obj, 'user') and obj.user == user:
            return True

        # SACCO admin can access any resource in their SACCO
        if hasattr(obj, 'sacco'):
            return user.roles.filter(
                name=Role.SACCO_ADMIN,
                sacco=obj.sacco,
            ).exists()

        return False


class IsEligibleGuarantor(BasePermission):
    """
    Verify user is eligible to guarantee a loan.

    Eligibility checks:
    - User is not the loan applicant
    - User has APPROVED membership in the same SACCO
    - User has savings > 0 in that SACCO
    - User is not already a guarantor on this loan
    """

    message = 'You are not eligible to be a guarantor for this loan.'

    def has_object_permission(self, request, view, obj):
        """
        Check all guarantor eligibility criteria.

        Object must be a Loan with .membership.user and .membership.sacco.
        """
        user = request.user

        if not user or not user.is_authenticated:
            return False

        # Import here to avoid circular imports
        from saccomembership.models import Membership

        # Determine loan and its applicant
        if (
            not hasattr(obj, 'membership')
            or not hasattr(obj.membership, 'user')
        ):
            return False

        loan_applicant = obj.membership.user
        sacco = obj.membership.sacco

        # Check 1: User cannot guarantee their own loan
        if user == loan_applicant:
            return False

        # Check 2: User must have APPROVED membership in same SACCO
        user_membership = Membership.objects.filter(
            user=user,
            sacco=sacco,
            status='APPROVED',
        ).first()

        if not user_membership:
            return False

        # Check 3: User must have savings > 0 in that SACCO
        try:
            from payments.models import Savings

            savings = Savings.objects.filter(
                user=user,
                sacco=sacco,
            ).first()
            if not savings or savings.amount <= 0:
                return False
        except ImportError:
            # If Savings model doesn't exist, skip this check
            pass

        # Check 4: User not already a guarantor on this loan
        try:
            from payments.models import LoanGuarantor

            guarantor_exists = LoanGuarantor.objects.filter(
                loan=obj,
                guarantor=user,
            ).exists()
            if guarantor_exists:
                return False
        except ImportError:
            # If LoanGuarantor model doesn't exist, skip this check
            pass

        return True


class GuarantorCapacityCheck(BasePermission):
    """
    Verify user has sufficient guarantee capacity for a loan.

    Checks that user's available guarantee capacity is at least
    10% of loan amount.
    """

    message = 'Insufficient guarantee capacity.'

    def has_object_permission(self, request, view, obj):
        """
        Check if user has sufficient guarantee capacity.

        Object must be a Loan with .amount attribute.
        Requires GuaranteeCapacity model to exist.
        """
        user = request.user

        if not user or not user.is_authenticated:
            return False

        if not hasattr(obj, 'amount'):
            return False

        try:
            from payments.models import GuaranteeCapacity

            capacity = GuaranteeCapacity.objects.filter(
                user=user
            ).first()

            if not capacity:
                return False

            required_capacity = obj.amount * Decimal('0.10')
            return capacity.available_capacity >= required_capacity

        except ImportError:
            # If GuaranteeCapacity model doesn't exist, deny access
            return False


# ============================================================
# REVIEW — READ THIS THEN DELETE FROM THIS LINE TO THE END
# ============================================================
#
# WHAT EACH CLASS DOES AND WHY:
#
# 1. IsKYCVerified
#    - Checks that user.kyc.status == 'APPROVED' before allowing access
#    - WHY: Financial services need to verify user identity (KYC = Know Your
#      Customer). Don't let unverified users access payments/loans.
#    - Used at view level only (has_permission method)
#
# 2. IsPhoneVerified
#    - Checks that user has a phone_number field set
#    - WHY: Phone needed for 2FA, M-Pesa callbacks, SMS notifications
#    - Used at view level only (has_permission method)
#
# 3. IsSaccoAdmin
#    - At view level: checks if user has ANY SACCO_ADMIN role
#    - At object level: checks if user has SACCO_ADMIN role FOR THAT SPECIFIC SACCO
#    - WHY: SACCO admins can manage members/loans/savings only within their SACCO.
#      Object-level prevents admin of Sacco A from touching Sacco B's data.
#
# 4. IsSuperAdmin
#    - Checks if user.is_staff OR has SUPER_ADMIN role
#    - WHY: Two-tiered super admin: Django staff (infrastructure) or SACCO platform
#      SUPER_ADMIN (business). Both have full access.
#
# 5. IsSaccoAdminOrSuperAdmin
#    - Combination of IsSaccoAdmin and IsSuperAdmin
#    - At object level: SUPER_ADMIN bypasses sacco check; SACCO_ADMIN must own sacco
#    - WHY: Cleaner permission checks for endpoints that both can access
#
# 6. IsMemberOfSacco
#    - Checks user has APPROVED membership in obj's SACCO
#    - Object can have .sacco or .membership.sacco attributes
#    - WHY: Members-only endpoints. Only approved members can access
#
# 7. IsOwnerOrSaccoAdmin
#    - True if: obj.user == request.user OR user is SACCO admin for obj.sacco
#    - WHY: Users can edit their own data; SACCO admins can edit any member's data
#      in their SACCO
#
# 8. IsEligibleGuarantor
#    - Four checks: (1) not applicant, (2) approved member in sacco,
#      (3) has savings > 0, (4) not already guarantor
#    - WHY: Loan guarantors must be financially healthy members who haven't
#      over-committed
#
# 9. GuarantorCapacityCheck
#    - Checks available_capacity >= 10% of loan amount
#    - WHY: Prevent guarantor from over-committing. Capacity = max they can
#      guarantee. At least 10% of loan needed to be meaningful
#
#
# DJANGO/PYTHON CONCEPTS:
#
# - BasePermission: Base class for all permission checks. Two methods:
#   * has_permission(request, view): View-level check, runs first
#   * has_object_permission(request, view, obj): Object-level check, runs for
#     detail views after fetching the object
#
# - permission_classes = [IsAuthenticated, IsSuperAdmin]: ALL must pass.
#   If any fails, request denied with 403 Forbidden.
#
# - Foreign Key queries with .filter(): Look up related objects efficiently.
#   user.roles.filter(name=Role.SACCO_ADMIN) finds all SACCO_ADMIN roles
#   for that user.
#
# - hasattr(obj, 'sacco'): Safely check if object has an attribute before
#   accessing it. Prevents AttributeError crashes.
#
# - Try/except ImportError: Some models may not be defined yet.
#   Gracefully skip checks if imports fail (e.g., GuaranteeCapacity).
#
# - Decimal('0.10'): Use Decimal for money calculations, not float.
#   Prevents floating-point rounding errors (0.1 + 0.2 != 0.3 in float!)
#
# - .exists(): Efficient check. Returns boolean without fetching data.
#   Better than if .first(): which fetches object from DB.
#
#
# HOW TO TEST MANUALLY:
#
# 1. Create two users: alice (member) and bob (sacco_admin)
# 2. Create a Sacco and assign:
#    - Role: bob -> SACCO_ADMIN, sacco=test_sacco
#    - Membership: alice -> APPROVED, sacco=test_sacco
# 3. In Django shell or via API:
#    - Call an endpoint with IsSaccoAdmin permission as alice: should fail (403)
#    - Call same endpoint as bob: should pass (200)
# 4. Test object-level: bob is SACCO_ADMIN for Sacco A. Try to access
#    a member resource in Sacco B: should fail (403)
# 5. Test IsOwnerOrSaccoAdmin: alice updates her own profile: pass.
#    bob (SACCO_ADMIN) updates alice's profile: pass.
#
#
# KEY DESIGN DECISIONS AND WHY:
#
# - Multiple inheritance of permissions: Use [IsSaccoAdmin, IsSuperAdmin] in
#   permission_classes. Both must pass (AND logic). For OR logic, create
#   combined classes like IsSaccoAdminOrSuperAdmin.
#
# - Object-level permissions are optional but crucial for multi-tenant systems.
#   Without them, a SACCO admin can see all SACCOs. With them, they see only
#   their SACCO.
#
# - Circular imports avoided by importing inside methods: Django apps reference
#   each other. Importing at top level can cause "django.apps.AppNotReady" errors.
#   Importing inside has_permission() is safe.
#
# - try/except ImportError for optional models: Some models (Savings,
#   GuaranteeCapacity) may be added later. Don't crash if missing; just skip
#   the check gracefully.
#
# - SUPER_ADMIN always bypasses sacco context: Platform admins need full access.
#   SACCO_ADMIN is scoped by sacco. Two-tier architecture.
#
# - .first() returns None if no match, so always check: if not capacity: return False
#   prevents AttributeError when accessing .available_capacity on None.
#
# ============================================================
# END OF REVIEW — DELETE EVERYTHING FROM THE FIRST # LINE ABOVE
# ============================================================
