from rest_framework.permissions import BasePermission

from saccomanagement.models import Role
from services.models import Guarantor, Saving


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
        if hasattr(obj, 'sacco'):
            sacco = obj.sacco
        elif hasattr(obj, 'membership') and hasattr(obj.membership, 'sacco'):
            sacco = obj.membership.sacco
        else:
            return False

        user = request.user

        if not user or not user.is_authenticated:
            return False

        return user.roles.filter(
            name=Role.SACCO_ADMIN,
            sacco=sacco,
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
        has_savings = Saving.objects.filter(
            membership=user_membership,
            status=Saving.Status.ACTIVE,
            amount__gt=0,
        ).exists()
        if not has_savings:
            return False

        # Check 4: User not already a guarantor on this loan
        guarantor_exists = Guarantor.objects.filter(
            loan=obj,
            guarantor=user,
        ).exists()
        if guarantor_exists:
            return False

        return True

