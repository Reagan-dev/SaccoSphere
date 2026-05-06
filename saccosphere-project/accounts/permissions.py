from rest_framework.permissions import BasePermission


class IsKYCVerified(BasePermission):
    """
    Allow access only when the authenticated user's KYC is approved.
    """

    message = 'You must complete KYC verification first.'

    def has_permission(self, request, view):
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
        user = request.user

        if not user or not user.is_authenticated:
            return False

        return bool(user.phone_number)


class IsSuperAdmin(BasePermission):
    """
    Allow access only to users with SUPER_ADMIN role.
    """

    message = 'Only super admins can access this resource.'

    def has_permission(self, request, view):
        user = request.user

        if not user or not user.is_authenticated:
            return False

        # Import here to avoid circular imports
        from saccomanagement.models import Role

        return user.roles.filter(name=Role.SUPER_ADMIN).exists()


class IsSaccoAdmin(BasePermission):
    """
    Allow access to users with SACCO_ADMIN or SUPER_ADMIN role.
    """

    message = 'Only sacco admins or super admins can access this resource.'

    def has_permission(self, request, view):
        user = request.user

        if not user or not user.is_authenticated:
            return False

        # Import here to avoid circular imports
        from saccomanagement.models import Role

        return user.roles.filter(
            name__in=[Role.SACCO_ADMIN, Role.SUPER_ADMIN]
        ).exists()
