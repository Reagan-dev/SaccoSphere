from rest_framework.exceptions import PermissionDenied

from saccomanagement.models import Role


class SaccoScopedMixin:
    """
    Mixin for views that need SACCO-scoped data filtering.

    Provides methods to filter querysets based on the current SACCO context.
    Only SACCO_ADMIN and SUPER_ADMIN can use these views.
    """

    def get_sacco_context(self):
        """
        Get the current SACCO context from the request.

        Returns the SACCO instance or None if SUPER_ADMIN or unauthenticated.
        """
        return getattr(self.request, 'current_sacco', None)

    def get_sacco_queryset(self, queryset, sacco_field='sacco'):
        """
        Filter queryset by SACCO context.

        Args:
            queryset: The base queryset to filter
            sacco_field: The name of the foreign key field to filter on
                        (default: 'sacco')

        Returns:
            Filtered queryset. Unchanged for SUPER_ADMIN, filtered for SACCO_ADMIN.

        Raises:
            PermissionDenied: If user is not SACCO_ADMIN or SUPER_ADMIN
        """
        user = self.request.user

        # SUPER_ADMIN: return unchanged
        if user.is_staff or user.roles.filter(
            name=Role.SUPER_ADMIN
        ).exists():
            return queryset

        # SACCO_ADMIN: filter by current SACCO
        if user.roles.filter(name=Role.SACCO_ADMIN).exists():
            current_sacco = self.get_sacco_context()
            if not current_sacco:
                raise PermissionDenied(
                    'SACCO context is required for this action.'
                )
            filter_kwargs = {sacco_field: current_sacco}
            return queryset.filter(**filter_kwargs)

        # MEMBER or other: deny access
        raise PermissionDenied(
            'Only SACCO admins can access this resource.'
        )

    def apply_sacco_scope(self, queryset):
        """
        Apply default SACCO scope to queryset.

        Shortcut for get_sacco_queryset with sacco_field='sacco'.

        Args:
            queryset: The base queryset to filter

        Returns:
            Filtered queryset
        """
        return self.get_sacco_queryset(queryset, sacco_field='sacco')
