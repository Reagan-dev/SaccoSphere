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
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsSaccoAdmin, IsSuperAdmin
from payments.models import Transaction
from saccomembership.membership_doc_serializers import (
    MembershipDocumentDetailSerializer,
)
from saccomembership.models import Membership, SaccoApplication
from saccomembership.serializers import MembershipListSerializer
from services.models import Loan, Saving

from .audit_logger import AuditMixin, log_audit
from .mixins import SaccoScopedMixin
from .models import Role, SystemAuditLog
from .odpc_logging import DataAccessMixin
from .serializers import (
    AdminMemberDetailSerializer,
    AdminSaccoStatsSerializer,
    ApplicationReviewSerializer,
    SystemAuditLogSerializer,
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


class AdminMemberDetailView(DataAccessMixin, SaccoScopedMixin, RetrieveAPIView):
    """Return one SACCO member with admin dashboard detail."""

    serializer_class = AdminMemberDetailSerializer
    permission_classes = [IsAuthenticated, IsSaccoAdmin]
    lookup_field = 'id'
    lookup_url_kwarg = 'membership_id'
    data_access_type = 'MEMBER_PROFILE'
    data_access_reason = 'Admin member detail view'

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
        self._log_object_access(self.object, request)
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
                Loan.Status.PENDING_APPROVAL,
                Loan.Status.UNDER_REVIEW,
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


class ApplicationReviewView(AuditMixin, SaccoScopedMixin, UpdateAPIView):
    """Approve or reject a SACCO membership application."""

    serializer_class = ApplicationReviewSerializer
    permission_classes = [IsAuthenticated, IsSaccoAdmin]
    lookup_field = 'id'
    audit_resource_type = 'SaccoApplication'

    def patch(self, request, *args, **kwargs):
        """Set SACCO context before reviewing an application."""
        response = self._set_sacco_context()
        if response:
            return response
        return super().patch(request, *args, **kwargs)

    def get(self, request, *args, **kwargs):
        """Return one SACCO application with uploaded documents."""
        response = self._set_sacco_context()
        if response:
            return response

        application = self.get_object()
        return Response(
            self._serialize_review_response(application, request),
            status=status.HTTP_200_OK,
        )

    def get_queryset(self):
        """Get applications scoped to the current SACCO."""
        queryset = SaccoApplication.objects.select_related(
            'user',
            'sacco',
            'reviewed_by',
        ).prefetch_related('membership_documents')
        return self.get_sacco_queryset(queryset, sacco_field='sacco')

    @transaction.atomic
    def partial_update(self, request, *args, **kwargs):
        """Apply an admin review decision to an application."""
        application = self.get_object()
        old_values = {
            'status': application.status,
            'review_notes': application.review_notes,
            'reviewed_by_id': str(application.reviewed_by_id)
            if application.reviewed_by_id
            else None,
            'reviewed_at': application.reviewed_at.isoformat()
            if application.reviewed_at
            else None,
        }
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
        log_audit(
            request.user,
            'UPDATE',
            self.audit_resource_type,
            application.id,
            old_values=old_values,
            new_values={
                'status': application.status,
                'review_notes': application.review_notes,
                'reviewed_by_id': str(application.reviewed_by_id),
                'reviewed_at': application.reviewed_at.isoformat(),
                'membership_id': str(membership.id) if membership else None,
            },
            request=request,
        )

        data = self._serialize_review_response(application, request)
        data['membership_id'] = str(membership.id) if membership else None
        return Response(data, status=status.HTTP_200_OK)

    def _serialize_review_response(self, application, request):
        documents = application.membership_documents.all()
        document_serializer = MembershipDocumentDetailSerializer(
            documents,
            many=True,
            context={'request': request},
        )
        return {
            'id': str(application.id),
            'user_id': str(application.user_id),
            'sacco_id': str(application.sacco_id),
            'application_type': application.application_type,
            'employment_status': application.employment_status,
            'employer_name': application.employer_name,
            'monthly_income': application.monthly_income,
            'additional_docs': application.additional_docs,
            'registration_fee_paid': application.registration_fee_paid,
            'status': application.status,
            'reviewed_by_id': (
                str(application.reviewed_by_id)
                if application.reviewed_by_id
                else None
            ),
            'review_notes': application.review_notes,
            'submitted_at': application.submitted_at,
            'reviewed_at': application.reviewed_at,
            'created_at': application.created_at,
            'updated_at': application.updated_at,
            'membership_documents': document_serializer.data,
        }

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


class AuditLogListView(ListAPIView):
    """List system audit logs for super admins."""

    serializer_class = SystemAuditLogSerializer
    permission_classes = [IsAuthenticated, IsSuperAdmin]

    def get_queryset(self):
        queryset = SystemAuditLog.objects.select_related('user').order_by(
            '-created_at',
        )
        action = self.request.query_params.get('action')
        resource_type = self.request.query_params.get('resource_type')
        user = self.request.query_params.get('user')

        if action:
            queryset = queryset.filter(action=action)
        if resource_type:
            queryset = queryset.filter(resource_type=resource_type)
        if user:
            queryset = queryset.filter(user__email__icontains=user)

        return queryset



