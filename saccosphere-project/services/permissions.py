"""Permissions for services app (loans, guarantors, savings)."""

from rest_framework.permissions import BasePermission

from .models import GuaranteeCapacity


class GuarantorCapacityCheck(BasePermission):
    """
    Allow guarantor response only if user has sufficient guarantee capacity.

    This permission checks whether the authenticated user (acting as guarantor)
    has enough available guarantee capacity to cover the loan amount. Used
    only when approving a guarantee request.
    """

    message = (
        'Insufficient guarantee capacity. Your available capacity is less '
        'than the requested guarantee amount.'
    )

    def has_permission(self, request, view):
        """
        Check if user has sufficient guarantee capacity.

        This is a view-level check. Object-level checks happen in the view.
        """
        user = request.user

        if not user or not user.is_authenticated:
            return False

        try:
            capacity = GuaranteeCapacity.objects.get(user=user)
            return capacity.available_capacity > 0
        except GuaranteeCapacity.DoesNotExist:
            return False

    def has_object_permission(self, request, view, obj):
        """
        Check if user has capacity for this specific guarantee.

        obj is the Guarantor instance. We check if the guarantor user
        has enough capacity for the guarantee_amount.
        """
        user = request.user
        guarantee = obj

        if not user or not user.is_authenticated:
            return False

        if guarantee.guarantor != user:
            return False

        try:
            capacity = GuaranteeCapacity.objects.get(user=user)
            return capacity.available_capacity >= guarantee.guarantee_amount
        except GuaranteeCapacity.DoesNotExist:
            return False
