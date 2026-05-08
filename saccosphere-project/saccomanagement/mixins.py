from django.http import JsonResponse
from rest_framework.exceptions import PermissionDenied

from saccomanagement.models import Role


class SaccoScopedMixin:
    """
    Mixin for views that need SACCO-scoped data filtering.

    Provides methods to filter querysets based on the current SACCO context.
    Only SACCO_ADMIN and SUPER_ADMIN can use these views.
    """

    def _set_sacco_context(self):
        """
        Set SACCO context from X-Sacco-ID header or first SACCO_ADMIN role.
        
        Called before get_queryset to validate and set the SACCO context.
        Returns JsonResponse with 403 if unauthorized, None otherwise.
        """
        user = self.request.user
        
        # Initialize as None
        self.request.current_sacco = None
        
        # SUPER_ADMIN sees all data
        if user.is_staff or user.roles.filter(name=Role.SUPER_ADMIN).exists():
            return None
        
        # Get all SACCO_ADMIN roles
        admin_roles = user.roles.filter(
            name=Role.SACCO_ADMIN,
            sacco__isnull=False,
        ).select_related('sacco')
        
        if not admin_roles.exists():
            # Not a SACCO admin
            return None
        
        # Check for X-Sacco-ID header
        sacco_id = self.request.headers.get('X-Sacco-ID')
        
        if sacco_id:
            # Validate the header-specified SACCO
            role = admin_roles.filter(sacco_id=sacco_id).first()
            if not role:
                return JsonResponse(
                    {
                        'success': False,
                        'message': 'You do not have access to this SACCO.',
                        'error_code': 'UNAUTHORIZED_SACCO',
                    },
                    status=403,
                )
            self.request.current_sacco = role.sacco
            return None
        
        # No header: use first SACCO_ADMIN role
        role = admin_roles.first()
        if role:
            self.request.current_sacco = role.sacco
        
        return None

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
