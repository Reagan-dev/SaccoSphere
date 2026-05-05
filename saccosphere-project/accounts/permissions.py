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
