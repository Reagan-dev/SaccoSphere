import logging
import time
import sys

from asgiref.local import Local
from django.http import JsonResponse
from django.utils.deprecation import MiddlewareMixin

from .utils import get_request_id


logger = logging.getLogger('saccosphere.access')
_request_context = Local()
request_logger = logging.getLogger('saccosphere.requests')


def get_current_sacco_id():
    return getattr(_request_context, 'sacco_id', None)


def get_current_correlation_id():
    return getattr(_request_context, 'correlation_id', None)


class RequestCorrelationMiddleware(MiddlewareMixin):
    def process_request(self, request):
        print(f"[CORRELATION] Process request", flush=True)
        correlation_id = get_request_id(request)
        request.correlation_id = correlation_id
        _request_context.correlation_id = correlation_id

    def process_response(self, request, response):
        correlation_id = getattr(request, 'correlation_id', get_request_id(request))
        response['X-Correlation-ID'] = correlation_id
        _request_context.correlation_id = None
        return response


class LoggingMiddleware(MiddlewareMixin):
    def process_request(self, request):
        request.start_time = time.monotonic()

    def process_response(self, request, response):
        response_time_ms = round(
            (time.monotonic() - getattr(request, 'start_time', time.monotonic()))
            * 1000,
            2,
        )
        user = getattr(request, 'user', None)
        username = user.get_username() if user and user.is_authenticated else 'anonymous'

        request_logger.info(
            'method=%s path=%s user=%s status_code=%s '
            'response_time_ms=%s correlation_id=%s',
            request.method,
            request.path,
            username,
            response.status_code,
            response_time_ms,
            getattr(request, 'correlation_id', '-'),
        )
        return response


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
        print(f"[SACCO_MIDDLEWARE_START] Entering process_request", flush=True)
        
        # Initialize as None
        request.current_sacco = None
        
        print(f"[SACCO_MIDDLEWARE] Initialized current_sacco", flush=True)

        # Unauthenticated users get no SACCO context
        if not request.user or not request.user.is_authenticated:
            print(f"[SACCO_MIDDLEWARE] User not authenticated", flush=True)
            return None

        user = request.user
        print(f"[MIDDLEWARE] Processing request for user: {user.email}", flush=True)

        # Import here to avoid circular imports
        from saccomanagement.models import Role

        # SUPER_ADMIN (staff or SUPER_ADMIN role) sees all data
        if user.is_staff or user.roles.filter(
            name=Role.SUPER_ADMIN, is_active=True,
        ).exists():
            logger.info(
                f'SUPER_ADMIN access: {user.email} | '
                f'Path: {request.path}'
            )
            print(f"[MIDDLEWARE] User is SUPER_ADMIN", flush=True)
            return None

        # SACCO_ADMIN needs a specific SACCO context
        admin_roles = user.roles.filter(
            name=Role.SACCO_ADMIN,
            sacco__isnull=False,
            is_active=True,
        ).select_related('sacco')

        if not admin_roles.exists():
            # Not a SACCO admin, treat as member (no SACCO context)
            print(f"[MIDDLEWARE] User has no SACCO_ADMIN roles", flush=True)
            return None

        # Check for X-Sacco-ID header
        sacco_id = request.headers.get('X-Sacco-ID')
        print(f"[MIDDLEWARE] Admin roles found: {admin_roles.count()} | X-Sacco-ID header: {sacco_id}", flush=True)

        if sacco_id:
            # Validate header-specified SACCO
            role = admin_roles.filter(sacco_id=sacco_id).first()
            print(f"[MIDDLEWARE] Looking for role with sacco_id={sacco_id} | Found: {role}", flush=True)

            if not role:
                print(f"[MIDDLEWARE] Unauthorized access attempt to SACCO {sacco_id}", flush=True)
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

            print(f"[MIDDLEWARE] Setting current_sacco to {role.sacco.name}", flush=True)
            request.current_sacco = role.sacco
            logger.info(
                f'SACCO_ADMIN context: {user.email} | '
                f'SACCO: {role.sacco.name}'
            )
            return None

        # No header provided: use first active SACCO_ADMIN role
        role = admin_roles.first()
        if role:
            print(f"[MIDDLEWARE] No header, setting current_sacco to {role.sacco.name} (default)", flush=True)
            request.current_sacco = role.sacco
            logger.info(
                f'SACCO_ADMIN context (default): {user.email} | '
                f'SACCO: {role.sacco.name}'
            )

        return None

    def process_response(self, request, response):
        """Clean up context on response."""
        _request_context.sacco_id = None
        return response


CorrelationIdMiddleware = RequestCorrelationMiddleware
