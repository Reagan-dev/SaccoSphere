import logging

from django.http import JsonResponse
from django.utils.deprecation import MiddlewareMixin

from saccomanagement.models import Role


logger = logging.getLogger('saccosphere.access')


class SaccoContextMiddleware(MiddlewareMixin):
    """
    Multi-tenant SACCO context middleware.

    Determines and validates the current SACCO context for authenticated users.
    Enforces data isolation by setting request.current_sacco.

    Rules:
    - Unauthenticated: current_sacco = None
    - SUPER_ADMIN: current_sacco = None (sees all data)
    - SACCO_ADMIN: current_sacco = Sacco from X-Sacco-ID header or first active role
    - MEMBER: current_sacco = None (filtered by own memberships in views)

    X-Sacco-ID header must match a SACCO_ADMIN role for that user.
    """

    def process_request(self, request):
        """
        Set SACCO context on the request.

        Called for every request. Runs after authentication middleware.
        """
        # Initialize as None
        request.current_sacco = None

        # Unauthenticated users get no SACCO context
        if not request.user or not request.user.is_authenticated:
            return None

        user = request.user

        # SUPER_ADMIN (staff or SUPER_ADMIN role) sees all data
        if user.is_staff or user.roles.filter(
            name=Role.SUPER_ADMIN
        ).exists():
            logger.info(
                f'SUPER_ADMIN access: {user.email} | '
                f'Path: {request.path}'
            )
            return None

        # SACCO_ADMIN needs a specific SACCO context
        admin_roles = user.roles.filter(
            name=Role.SACCO_ADMIN,
            sacco__isnull=False,
        ).select_related('sacco')

        if not admin_roles.exists():
            # Not a SACCO admin, treat as member (no SACCO context)
            return None

        # Check for X-Sacco-ID header
        sacco_id = request.headers.get('X-Sacco-ID')

        if sacco_id:
            # Validate header-specified SACCO
            role = admin_roles.filter(sacco_id=sacco_id).first()

            if not role:
                logger.warning(
                    f'SACCO_ADMIN {user.email} attempted access to '
                    f'unauthorized SACCO {sacco_id}'
                )
                return JsonResponse(
                    {
                        'success': False,
                        'message': 'You do not have access to this SACCO.',
                        'error_code': 'UNAUTHORIZED_SACCO',
                    },
                    status=403,
                )

            request.current_sacco = role.sacco
            logger.info(
                f'SACCO_ADMIN context: {user.email} | '
                f'SACCO: {role.sacco.name}'
            )
            return None

        # No header provided: use first active SACCO_ADMIN role
        role = admin_roles.first()
        if role:
            request.current_sacco = role.sacco
            logger.info(
                f'SACCO_ADMIN context (default): {user.email} | '
                f'SACCO: {role.sacco.name}'
            )

        return None
