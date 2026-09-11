from rest_framework.exceptions import APIException, PermissionDenied
from rest_framework.permissions import SAFE_METHODS

from saccomanagement.models import Role


class SaccoHeaderRequired(APIException):
    """A write/money endpoint was hit without an explicit ``X-Sacco-ID``.

    Tenancy on this platform is row-level in a shared schema, so the
    "no header -> use the admin's first SACCO_ADMIN role" fallback lets a
    multi-SACCO admin silently execute a write (e.g. a dividend
    disbursement) against an arbitrary SACCO. For endpoints that opt in
    with ``require_sacco_header = True`` we fail loudly with a 400
    instead.
    """

    status_code = 400
    default_detail = 'X-Sacco-ID header is required for this operation.'
    default_code = 'sacco_header_required'


class SaccoScopedMixin:
    """
    Mixin for views that need SACCO-scoped data filtering.

    Provides methods to filter querysets based on the current SACCO context.
    Only SACCO_ADMIN and SUPER_ADMIN can use these views.

    Set ``require_sacco_header = True`` on a view to forbid the silent
    "first SACCO_ADMIN role" fallback for unsafe methods
    (POST/PUT/PATCH/DELETE) when the admin administers more than one
    SACCO - such a request must carry ``X-Sacco-ID`` or it gets a clean
    400. Safe methods and single-SACCO admins are unaffected. Turn it on
    for every endpoint that writes or moves money.

    Also set ``require_sacco_header_for_reads = True`` when a *read* on
    the view can itself hand back another tenant's data on a silent
    guess (e.g. B2C disbursement status/history) - this extends the same
    check to safe methods too. Off by default: existing
    ``require_sacco_header = True`` views (dividends, savings admin,
    loan status) deliberately keep the fallback for their own reads, and
    this flag never changes that.
    """

    require_sacco_header = False
    require_sacco_header_for_reads = False

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if self.should_enforce_sacco_scope():
            self._set_sacco_context()

    def should_enforce_sacco_scope(self):
        return True

    def _set_sacco_context(self):
        """
        Set SACCO context from X-Sacco-ID header or first SACCO_ADMIN role.

        Raises PermissionDenied immediately if a valid sacco context cannot be
        established. SUPER_ADMIN users are allowed without a SACCO context.
        """
        user = self.request.user

        # SUPER_ADMIN sees all data - no context needed
        if user.is_staff or user.roles.filter(
            name=Role.SUPER_ADMIN, is_active=True,
        ).exists():
            self.request.current_sacco = None
            return

        # Get all SACCO_ADMIN roles
        admin_roles = user.roles.filter(
            name=Role.SACCO_ADMIN,
            sacco__isnull=False,
            is_active=True,
        ).select_related('sacco')

        if not admin_roles.exists():
            raise PermissionDenied(
                'Only SACCO admins can access this resource.'
            )

        # Check for X-Sacco-ID header
        sacco_id = self.request.headers.get('X-Sacco-ID')

        if sacco_id:
            # Validate the header-specified SACCO
            role = admin_roles.filter(sacco_id=sacco_id).first()
            if not role:
                raise PermissionDenied(
                    'You do not have access to this SACCO.'
                )
            self.request.current_sacco = role.sacco
            return

        # No header sent. Falling back to the admin's SACCO_ADMIN role is
        # only unambiguous when they have exactly one; for a write/money
        # endpoint (require_sacco_header) a multi-SACCO admin must be
        # explicit rather than have the write land on an arbitrary SACCO.
        # require_sacco_header_for_reads additionally drops the
        # safe-method exemption, for views where a read is itself
        # sensitive enough that a silent guess is unacceptable.
        if (
            self.require_sacco_header
            and admin_roles.count() > 1
            and (
                self.require_sacco_header_for_reads
                or self.request.method not in SAFE_METHODS
            )
        ):
            raise SaccoHeaderRequired()

        role = admin_roles.first()
        if role:
            self.request.current_sacco = role.sacco
            return

        # This should not be reached due to the admin_roles.exists() check
        # above, but raise for safety.
        raise PermissionDenied('SACCO context is required for this action.')

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
            name=Role.SUPER_ADMIN, is_active=True,
        ).exists():
            return queryset

        # SACCO_ADMIN: filter by current SACCO
        if user.roles.filter(
            name=Role.SACCO_ADMIN, is_active=True,
        ).exists():
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
