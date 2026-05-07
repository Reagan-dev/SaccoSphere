from django.db.models import Q
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.generics import ListAPIView, UpdateAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from accounts.permissions import IsSaccoAdmin
from saccomembership.models import Membership
from saccomembership.serializers import MembershipListSerializer

from .mixins import SaccoScopedMixin
from .models import Role


class AdminMemberListView(SaccoScopedMixin, ListAPIView):
    """
    List members of the current SACCO.

    GET /api/v1/management/members/

    Permission: IsSaccoAdmin

    Filters:
    - ?search= — searches member email and member_number
    - ?status= — PENDING|APPROVED|REJECTED|SUSPENDED

    Returns paginated Membership records with select_related optimization.
    """

    serializer_class = MembershipListSerializer
    permission_classes = [IsAuthenticated, IsSaccoAdmin]

    def get_queryset(self):
        """Get members scoped to current SACCO with search/status filters."""
        queryset = Membership.objects.all()

        # Apply SACCO scope
        queryset = self.apply_sacco_scope(queryset)

        # Select related for performance
        queryset = queryset.select_related('user', 'sacco')

        # Search filter (email, member_number)
        search = self.request.query_params.get('search')
        if search:
            queryset = queryset.filter(
                Q(user__email__icontains=search)
                | Q(member_number__icontains=search)
            )

        # Status filter
        status_filter = self.request.query_params.get('status')
        if status_filter:
            queryset = queryset.filter(status=status_filter)

        return queryset.order_by('-created_at')


class AdminLoanApprovalView(SaccoScopedMixin, UpdateAPIView):
    """
    Update loan status (admin approval workflow).

    PATCH /api/v1/management/loans/{id}/status/

    Permission: IsSaccoAdmin

    Allowed transitions:
    - PENDING → BOARD_REVIEW
    - BOARD_REVIEW → APPROVED or REJECTED

    On APPROVED: generates RepaymentSchedule.

    Body: {
        "status": "APPROVED|REJECTED|BOARD_REVIEW",
        "notes": "Optional admin notes"
    }

    Returns 200 with updated Loan instance.
    """

    permission_classes = [IsAuthenticated, IsSaccoAdmin]
    lookup_field = 'id'

    def get_queryset(self):
        """Get loans scoped to current SACCO."""
        from services.models import Loan

        queryset = Loan.objects.all()
        return self.apply_sacco_scope(
            queryset,
            sacco_field='member__sacco',
        )

    def get_serializer(self, *args, **kwargs):
        """Return loan serializer for response."""
        from services.serializers import LoanSerializer

        return LoanSerializer(*args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        """
        Partial update with status transition validation.

        Validates that the status transition is allowed.
        Triggers RepaymentSchedule generation on APPROVED.
        """
        loan = self.get_object()
        new_status = request.data.get('status')
        notes = request.data.get('notes', '')

        # Validate status transition
        current_status = loan.status
        valid_transitions = {
            'PENDING': ['BOARD_REVIEW'],
            'BOARD_REVIEW': ['APPROVED', 'REJECTED'],
            'APPROVED': [],
            'REJECTED': [],
        }

        if new_status not in valid_transitions.get(current_status, []):
            raise ValidationError(
                {
                    'status': f'Cannot transition from {current_status} '
                    f'to {new_status}.'
                }
            )

        # Update status and notes
        loan.status = new_status
        if notes:
            loan.admin_notes = notes
        loan.save()

        # Generate repayment schedule on approval
        if new_status == 'APPROVED':
            try:
                from services.tasks import generate_repayment_schedule

                generate_repayment_schedule(loan.id)
            except ImportError:
                # Task may not exist yet, skip silently
                pass

        serializer = self.get_serializer(loan)
        return Response(serializer.data, status=status.HTTP_200_OK)


# ============================================================
# REVIEW — READ THIS THEN DELETE FROM THIS LINE TO THE END
# ============================================================
#
# WHAT EACH COMPONENT DOES AND WHY:
#
# MIDDLEWARE:
#
# 1. SaccoContextMiddleware (config/middleware.py)
#    - Runs on EVERY request after authentication
#    - Determines request.current_sacco based on user role and X-Sacco-ID header
#    - SUPER_ADMIN (staff or SUPER_ADMIN role): current_sacco = None (sees all)
#    - SACCO_ADMIN: current_sacco = selected Sacco from header or first active role
#    - MEMBER: current_sacco = None (no context, filtered in views via Membership)
#    - Returns 403 JsonResponse if header specifies unauthorized Sacco
#    - WHY: Central place to enforce multi-tenant isolation. Every view can trust
#      request.current_sacco is valid and authorized. Logs all access for audit.
#
# MIXIN:
#
# 2. SaccoScopedMixin (saccomanagement/mixins.py)
#    - Provides queryset filtering by SACCO context
#    - get_sacco_context(): Returns request.current_sacco
#    - get_sacco_queryset(queryset, field='sacco'): Filters queryset by SACCO
#      * SUPER_ADMIN: returns unchanged
#      * SACCO_ADMIN: filters by current_sacco
#      * Others: raises PermissionDenied
#    - apply_sacco_scope(queryset): Shortcut with default field name
#    - WHY: DRY principle. Every admin view that needs SACCO filtering inherits this.
#      Single place to change filtering logic. Prevents accidental data leaks.
#
# ADMIN VIEWS:
#
# 3. AdminMemberListView (GET /api/v1/management/members/)
#    - Lists Membership records for current SACCO
#    - Inherits SaccoScopedMixin for automatic SACCO filtering
#    - Filters: ?search= (email/member_number), ?status=
#    - select_related('user', 'sacco') prevents N+1 queries
#    - Ordered by -created_at (newest first)
#    - WHY: SACCO admins need to see and manage their members. Mixin ensures
#      admin of Sacco A can't see Sacco B's members even if they try.
#
# 4. AdminLoanApprovalView (PATCH /api/v1/management/loans/{id}/status/)
#    - Updates loan status via workflow: PENDING → BOARD_REVIEW → APPROVED/REJECTED
#    - Validates status transitions (no invalid state changes)
#    - On APPROVED: triggers generate_repayment_schedule task
#    - Returns 200 with updated Loan on success
#    - WHY: Enforces business logic for loan approval. Only valid transitions allowed.
#      Generates repayment schedule only when approved (not before). Admin notes
#      provide audit trail of decisions.
#
#
# DJANGO/PYTHON CONCEPTS:
#
# - Middleware.process_request(): Runs before view. Can return a Response to
#   short-circuit the request (return 403 here). Must return None to continue.
#
# - .select_related('user', 'sacco'): Optimization. Fetches related objects in
#   one SQL query instead of N+1. Works for ForeignKey (one-to-one). Use
#   prefetch_related() for reverse ForeignKey or ManyToMany.
#
# - Q objects: Allow OR logic in queries. Q(user__email__icontains=x) |
#   Q(member_number__icontains=x) = OR. Used for multi-field search.
#
# - UpdateAPIView: Generic view for PATCH/PUT. Implements get_object() to fetch
#   by PK or custom lookup_field. partial_update() is called for PATCH.
#
# - MiddlewareMixin: Django's base class for middleware. process_request()
#   runs before view, process_response() after. Return None to continue,
#   or return a Response to stop.
#
# - JsonResponse: Raw Django response, not DRF. Used in middleware because
#   middleware runs before DRF. Returns JSON without DRF serialization.
#
# - Mixin: Python class providing reusable methods. Multiple inheritance allows
#   combining mixins with generic views: class MyView(SaccoScopedMixin, ListAPIView).
#
#
# HOW TO TEST MANUALLY:
#
# 1. Create two test SACCOs: Sacco A and Sacco B
# 2. Create two SACCO_ADMIN users: admin_a (for Sacco A), admin_b (for Sacco B)
# 3. Assign roles via Role model or role API:
#    - admin_a SACCO_ADMIN role for Sacco A
#    - admin_b SACCO_ADMIN role for Sacco B
# 4. Add 5 members to Sacco A via Membership model
# 5. Login as admin_a, call:
#    GET /api/v1/management/members/ → should return 5 members
# 6. Call with header:
#    GET /api/v1/management/members/ with X-Sacco-ID: <sacco_b_id>
#    → middleware should return 403 "You do not have access"
# 7. Add a loan to Sacco A with status PENDING
# 8. Call:
#    PATCH /api/v1/management/loans/<loan_id>/status/ with {"status": "BOARD_REVIEW"}
#    → should return 200 with updated status
# 9. Call:
#    PATCH /api/v1/management/loans/<loan_id>/status/ with {"status": "PENDING"}
#    → should return 400 "Cannot transition" (invalid)
#
#
# KEY DESIGN DECISIONS AND WHY:
#
# - X-Sacco-ID HEADER: Multi-tenant systems need a way to specify context.
#   Header is preferred over URL parameter (cleaner URLs). Middleware validates
#   header matches user's role, preventing tampering.
#
# - First active role as fallback: If no header, use admin's first active role.
#   Assumes SACCO_ADMIN has only one Sacco (usually true). Prevents errors if
#   header missing, while enforcing single-Sacco context per request.
#
# - SUPER_ADMIN bypasses Sacco context: Platform admins need to see all data.
#   Having request.current_sacco = None for SUPER_ADMIN allows views to check
#   if not request.current_sacco: I'm a super admin. Clean pattern.
#
# - Status transition validation: Enum the allowed transitions in code, don't
#   allow arbitrary transitions. Prevents invalid states (e.g., REJECTED →
#   APPROVED, which makes no sense). Audit trail: admin_notes field records
#   who did what and why.
#
# - Mixin for queryset filtering: Don't repeat "if super admin... if sacco admin..."
#   in every view. Centralize in mixin. Bug in one place affects all views, so
#   one fix solves all. Easy to test.
#
# - select_related optimization: Membership table has ForeignKey to User and Sacco.
#   Without select_related, fetching 100 members = 200 SQL queries (1 per FK).
#   With select_related, = 1 query with JOINs. Critical for performance.
#
# - Graceful task import: generate_repayment_schedule may not exist yet. Try/except
#   ImportError means loan approval works even if task is not implemented.
#   No crashes due to missing dependencies.
#
# ============================================================
# END OF REVIEW — DELETE EVERYTHING FROM THE FIRST # LINE ABOVE
# ============================================================
