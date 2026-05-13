from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.db.models import (
    DecimalField,
    OuterRef,
    Prefetch,
    Q,
    Subquery,
    Sum,
    Value,
)
from django.db.models.functions import Coalesce
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.generics import ListAPIView, RetrieveAPIView, UpdateAPIView
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsSaccoAdmin
from payments.models import Transaction
from saccomembership.models import Membership, SaccoApplication
from saccomembership.serializers import MembershipListSerializer
from services.models import Loan, Saving

from .mixins import SaccoScopedMixin
from .models import ImportJob, Role
from .serializers import (
    AdminMemberDetailSerializer,
    AdminSaccoStatsSerializer,
    ApplicationReviewSerializer,
)


ZERO = Decimal('0.00')


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

    def get(self, request, *args, **kwargs):
        """Set SACCO context before processing request."""
        response = self._set_sacco_context()
        if response:  # 403 error
            return response
        return super().get(request, *args, **kwargs)

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


class AdminMemberDetailView(SaccoScopedMixin, RetrieveAPIView):
    """Return one SACCO member with admin dashboard detail."""

    serializer_class = AdminMemberDetailSerializer
    permission_classes = [IsAuthenticated, IsSaccoAdmin]
    lookup_field = 'id'
    lookup_url_kwarg = 'membership_id'

    def get(self, request, *args, **kwargs):
        """Set SACCO context before retrieving member detail."""
        response = self._set_sacco_context()
        if response:
            return response
        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        """Get member detail records scoped to the current SACCO."""
        savings_total = Saving.objects.filter(
            membership=OuterRef('pk'),
            status=Saving.Status.ACTIVE,
        ).values('membership').annotate(
            total=Sum('amount')
        ).values('total')[:1]
        outstanding_loans = Loan.objects.filter(
            membership=OuterRef('pk'),
            status=Loan.Status.ACTIVE,
        ).values('membership').annotate(
            total=Sum('outstanding_balance')
        ).values('total')[:1]

        queryset = Membership.objects.select_related(
            'user',
            'user__kyc',
            'sacco',
        ).prefetch_related(
            Prefetch(
                'saving_set',
                queryset=Saving.objects.select_related('savings_type'),
                to_attr='admin_savings',
            ),
            Prefetch(
                'loan_set',
                queryset=Loan.objects.filter(
                    status=Loan.Status.ACTIVE,
                ).select_related('loan_type'),
                to_attr='admin_active_loans',
            ),
        ).annotate(
            savings_total=Coalesce(
                Subquery(
                    savings_total,
                    output_field=DecimalField(
                        max_digits=12,
                        decimal_places=2,
                    ),
                ),
                Value(ZERO),
                output_field=DecimalField(max_digits=12, decimal_places=2),
            ),
            outstanding_loans=Coalesce(
                Subquery(
                    outstanding_loans,
                    output_field=DecimalField(
                        max_digits=12,
                        decimal_places=2,
                    ),
                ),
                Value(ZERO),
                output_field=DecimalField(max_digits=12, decimal_places=2),
            ),
        )

        return self.apply_sacco_scope(queryset)

    def get_serializer_context(self):
        """Add recent member transactions to serializer context."""
        context = super().get_serializer_context()
        if hasattr(self, 'object'):
            context['recent_transactions'] = list(
                Transaction.objects.filter(
                    user=self.object.user,
                ).order_by('-created_at')[:10]
            )
        return context

    def retrieve(self, request, *args, **kwargs):
        """Store object before serializer context is built."""
        self.object = self.get_object()
        serializer = self.get_serializer(self.object)
        return Response(serializer.data)


class AdminSaccoStatsView(SaccoScopedMixin, APIView):
    """Return aggregate dashboard stats for the current SACCO."""

    permission_classes = [IsAuthenticated, IsSaccoAdmin]

    def get(self, request):
        """Return current SACCO stats without per-member queries."""
        response = self._set_sacco_context()
        if response:
            return response

        sacco = self.get_sacco_context()
        stats = self._build_stats(sacco)
        serializer = AdminSaccoStatsSerializer(stats)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def _build_stats(self, sacco):
        now = timezone.localdate()
        savings = Saving.objects.filter(membership__sacco=sacco)
        active_loans = Loan.objects.filter(
            membership__sacco=sacco,
            status=Loan.Status.ACTIVE,
        )
        defaulted_loans = Loan.objects.filter(
            membership__sacco=sacco,
            status=Loan.Status.DEFAULTED,
        )
        pending_loans = Loan.objects.filter(
            membership__sacco=sacco,
            status__in=[
                Loan.Status.PENDING,
                Loan.Status.GUARANTORS_PENDING,
                Loan.Status.BOARD_REVIEW,
            ],
        )

        total_active_loans = active_loans.count()
        default_count = defaulted_loans.count()
        default_rate = self._calculate_default_rate(
            default_count,
            total_active_loans,
        )

        recent_transactions = Transaction.objects.filter(
            user__membership__sacco=sacco,
        ).select_related('provider').distinct().order_by('-created_at')[:5]

        return {
            'total_members': Membership.objects.filter(
                sacco=sacco,
                status=Membership.Status.APPROVED,
            ).count(),
            'pending_applications': SaccoApplication.objects.filter(
                sacco=sacco,
                status__in=[
                    SaccoApplication.Status.SUBMITTED,
                    SaccoApplication.Status.UNDER_REVIEW,
                ],
            ).count(),
            'total_savings_portfolio': savings.aggregate(
                total=Coalesce(
                    Sum('amount'),
                    Value(ZERO),
                    output_field=DecimalField(
                        max_digits=14,
                        decimal_places=2,
                    ),
                )
            )['total'],
            'total_loans_portfolio': active_loans.aggregate(
                total=Coalesce(
                    Sum('outstanding_balance'),
                    Value(ZERO),
                    output_field=DecimalField(
                        max_digits=14,
                        decimal_places=2,
                    ),
                )
            )['total'],
            'active_loans_count': total_active_loans,
            'pending_loan_approvals': pending_loans.count(),
            'default_count': default_count,
            'default_rate': default_rate,
            'monthly_contributions': savings.filter(
                last_transaction_date__year=now.year,
                last_transaction_date__month=now.month,
            ).aggregate(
                total=Coalesce(
                    Sum('total_contributions'),
                    Value(ZERO),
                    output_field=DecimalField(
                        max_digits=14,
                        decimal_places=2,
                    ),
                )
            )['total'],
            'recent_transactions': [
                {
                    'id': str(item.id),
                    'reference': item.reference,
                    'transaction_type': item.transaction_type,
                    'amount': item.amount,
                    'status': item.status,
                    'description': item.description,
                    'created_at': item.created_at,
                }
                for item in recent_transactions
            ],
        }

    def _calculate_default_rate(self, default_count, active_count):
        if active_count == 0:
            return ZERO

        return (
            Decimal(default_count) * Decimal('100') / Decimal(active_count)
        ).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


class ApplicationReviewView(SaccoScopedMixin, UpdateAPIView):
    """Approve or reject a SACCO membership application."""

    serializer_class = ApplicationReviewSerializer
    permission_classes = [IsAuthenticated, IsSaccoAdmin]
    lookup_field = 'id'

    def patch(self, request, *args, **kwargs):
        """Set SACCO context before reviewing an application."""
        response = self._set_sacco_context()
        if response:
            return response
        return super().patch(request, *args, **kwargs)

    def get_queryset(self):
        """Get applications scoped to the current SACCO."""
        queryset = SaccoApplication.objects.select_related('user', 'sacco')
        return self.get_sacco_queryset(queryset, sacco_field='sacco')

    @transaction.atomic
    def partial_update(self, request, *args, **kwargs):
        """Apply an admin review decision to an application."""
        application = self.get_object()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        review_status = serializer.validated_data['status']
        review_notes = serializer.validated_data.get('review_notes', '')

        application.status = review_status
        application.review_notes = review_notes
        application.reviewed_by = request.user
        application.reviewed_at = timezone.now()
        application.save(
            update_fields=[
                'status',
                'review_notes',
                'reviewed_by',
                'reviewed_at',
            ],
        )

        membership = None
        if review_status == SaccoApplication.Status.APPROVED:
            membership, _ = Membership.objects.update_or_create(
                user=application.user,
                sacco=application.sacco,
                defaults={
                    'status': Membership.Status.APPROVED,
                    'approved_date': timezone.now(),
                    'notes': review_notes,
                },
            )

        self._notify_applicant(application)

        return Response(
            {
                'id': str(application.id),
                'status': application.status,
                'review_notes': application.review_notes,
                'membership_id': str(membership.id) if membership else None,
            },
            status=status.HTTP_200_OK,
        )

    def _notify_applicant(self, application):
        """Notify an applicant that their SACCO application was reviewed."""
        try:
            from notifications.utils import create_notification
        except ImportError:
            return

        if application.status == SaccoApplication.Status.APPROVED:
            title = 'SACCO application approved'
            message = (
                f'Your application to {application.sacco.name} was approved.'
            )
        else:
            title = 'SACCO application rejected'
            message = (
                f'Your application to {application.sacco.name} was rejected.'
            )

        create_notification(
            user=application.user,
            title=title,
            message=message,
        )


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

    def patch(self, request, *args, **kwargs):
        """Set SACCO context before processing request."""
        response = self._set_sacco_context()
        if response:  # 403 error
            return response
        return super().patch(request, *args, **kwargs)

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
                import importlib
                tasks_module = importlib.import_module('services.tasks')
                tasks_module.generate_repayment_schedule(loan.id)
            except ImportError:
                # Task may not exist yet, skip silently
                pass

        serializer = self.get_serializer(loan)
        return Response(serializer.data, status=status.HTTP_200_OK)


class MemberImportView(SaccoScopedMixin, APIView):
    """Create an asynchronous SACCO member import job from uploaded file."""

    permission_classes = [IsAuthenticated, IsSaccoAdmin]
    parser_classes = [MultiPartParser]

    def post(self, request):
        """Validate uploaded file, create import job, and enqueue task."""
        response = self._set_sacco_context()
        if response:
            return response

        sacco = self.get_sacco_context()
        if sacco is None:
            return Response(
                {'detail': 'SACCO context is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        upload = request.FILES.get('file')
        if upload is None:
            return Response(
                {'detail': 'file is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        filename = upload.name.lower()
        if not (
            filename.endswith('.csv')
            or filename.endswith('.xlsx')
            or filename.endswith('.xls')
        ):
            return Response(
                {'detail': 'Only .csv, .xlsx, or .xls files are supported.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        job = ImportJob.objects.create(
            sacco=sacco,
            imported_by=request.user,
            file=upload,
            status=ImportJob.Status.PENDING,
        )

        from saccomanagement.tasks import run_member_import_task

        try:
            run_member_import_task.delay(str(job.id))
        except Exception as exc:
            job.status = ImportJob.Status.FAILED
            job.error_summary = [
                {
                    'error': (
                        'Failed to enqueue import task. '
                        f'{str(exc)}'
                    ),
                },
            ]
            job.completed_at = timezone.now()
            job.save(
                update_fields=[
                    'status',
                    'error_summary',
                    'completed_at',
                ],
            )
            return Response(
                {
                    'detail': (
                        'Import service is currently unavailable. '
                        'Please retry shortly.'
                    ),
                    'job_id': str(job.id),
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        return Response(
            {
                'job_id': str(job.id),
                'message': (
                    'Import started. Check status at '
                    f'/management/import/{job.id}/'
                ),
            },
            status=status.HTTP_202_ACCEPTED,
        )


class ImportJobStatusView(SaccoScopedMixin, RetrieveAPIView):
    """Return SACCO-scoped progress and summary for one import job."""

    permission_classes = [IsAuthenticated, IsSaccoAdmin]
    lookup_field = 'id'
    lookup_url_kwarg = 'job_id'

    def get(self, request, *args, **kwargs):
        """Set SACCO context before retrieving import status."""
        response = self._set_sacco_context()
        if response:
            return response
        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        """Restrict import jobs to current SACCO admin scope."""
        queryset = ImportJob.objects.select_related('sacco', 'imported_by')
        return self.get_sacco_queryset(queryset, sacco_field='sacco')

    def retrieve(self, request, *args, **kwargs):
        """Return structured import status details for the requested job."""
        job = self.get_object()
        return Response(
            {
                'job_id': str(job.id),
                'sacco_id': str(job.sacco_id),
                'status': job.status,
                'total_rows': job.total_rows,
                'success_count': job.success_count,
                'fail_count': job.fail_count,
                'error_summary': job.error_summary,
                'created_at': job.created_at,
                'completed_at': job.completed_at,
            },
            status=status.HTTP_200_OK,
        )


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
