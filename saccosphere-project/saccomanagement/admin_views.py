"""SACCO admin loan approval views."""

from django.db import transaction

from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.generics import ListAPIView, UpdateAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.serializers import CharField, ChoiceField, Serializer

from accounts.permissions import IsSaccoAdmin
from guarantor.utils import check_loan_guarantors_complete
from notifications.models import Notification
from notifications.utils import create_notification
from services.models import Loan

from .audit_logger import log_audit
from .loan_utils import (
    LOAN_FINAL_STATUSES,
    build_guarantors_summary,
    get_member_application_documents,
    initiate_loan_disbursement,
    persist_loan_repayment_schedule,
)
from .mixins import SaccoScopedMixin


APPROVAL_QUEUE_STATUSES = [
    Loan.Status.PENDING_APPROVAL,
    Loan.Status.UNDER_REVIEW,
    Loan.Status.BOARD_REVIEW,
]


class LoanStatusUpdateSerializer(Serializer):
    """Validate admin loan status transition requests."""

    status = ChoiceField(
        choices=[
            Loan.Status.UNDER_REVIEW,
            Loan.Status.APPROVED,
            Loan.Status.REJECTED,
            Loan.Status.DISBURSED,
        ],
    )
    notes = CharField(required=False, allow_blank=True, max_length=500)
    override_reason = CharField(
        required=False,
        allow_blank=True,
        min_length=10,
        max_length=500,
        help_text='Required when approving loan with negative CRB listing.',
    )


class LoanApprovalListView(SaccoScopedMixin, ListAPIView):
    """
    List loans awaiting SACCO admin approval.

    GET /api/v1/management/loans/approvals/
    """

    permission_classes = [IsAuthenticated, IsSaccoAdmin]
    pagination_class = None

    def get(self, request, *args, **kwargs):
        response = self._set_sacco_context()
        if response:
            return response
        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        queryset = Loan.objects.filter(
            status__in=APPROVAL_QUEUE_STATUSES,
        ).select_related(
            'membership',
            'membership__user',
            'membership__sacco',
            'loan_type',
        ).prefetch_related(
            'guarantors',
            'external_guarantors',
        )
        return self.get_sacco_queryset(
            queryset,
            sacco_field='membership__sacco',
        ).order_by('-created_at')

    def list(self, request, *args, **kwargs):
        loans = self.get_queryset()
        results = [
            self._serialize_loan(loan, request)
            for loan in loans
        ]
        return Response(
            {
                'success': True,
                'count': len(results),
                'results': results,
            },
            status=status.HTTP_200_OK,
        )

    def _serialize_loan(self, loan, request):
        user = loan.membership.user
        member_name = user.get_full_name() or user.email
        
        # Get latest CRB check
        latest_crb = loan.crb_checks.order_by('-checked_at').first()
        
        return {
            'loan_id': str(loan.id),
            'member_name': member_name,
            'member_number': loan.membership.member_number,
            'loan_type_name': loan.loan_type.name if loan.loan_type else None,
            'amount': str(loan.amount),
            'term_months': loan.term_months,
            'application_notes': loan.application_notes,
            'applied_at': loan.created_at,
            'status': loan.status,
            'guarantors_summary': build_guarantors_summary(loan),
            'required_documents': get_member_application_documents(
                loan,
                request=request,
            ),
            'crb_status': latest_crb.band if latest_crb else None,
            'crb_score': latest_crb.score if latest_crb else None,
            'crb_checked_at': latest_crb.checked_at.isoformat() if latest_crb else None,
            'crb_listed_negative': latest_crb.listed_negative if latest_crb else None,
        }


class AdminLoanApprovalView(SaccoScopedMixin, UpdateAPIView):
    """
    Update loan status through the SACCO admin approval workflow.

    PATCH /api/v1/management/loans/<pk>/status/
    """

    permission_classes = [IsAuthenticated, IsSaccoAdmin]
    lookup_field = 'id'
    http_method_names = ['patch', 'head', 'options']

    def patch(self, request, *args, **kwargs):
        response = self._set_sacco_context()
        if response:
            return response
        return super().patch(request, *args, **kwargs)

    def get_queryset(self):
        queryset = Loan.objects.select_related(
            'membership',
            'membership__user',
            'membership__sacco',
            'loan_type',
        )
        return self.get_sacco_queryset(
            queryset,
            sacco_field='membership__sacco',
        )

    def partial_update(self, request, *args, **kwargs):
        loan = self.get_object()
        serializer = LoanStatusUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        new_status = serializer.validated_data['status']
        notes = serializer.validated_data.get('notes', '')
        current_status = loan.status

        self._validate_transition(current_status, new_status)

        old_values = {
            'status': loan.status,
            'admin_notes': loan.admin_notes,
            'rejection_reason': loan.rejection_reason,
        }

        if new_status == Loan.Status.APPROVED:
            # Check guarantors are complete
            is_complete, reason = check_loan_guarantors_complete(loan)
            if not is_complete:
                return Response(
                    {'detail': reason},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            
            # Check CRB check exists
            from services.models import CRBCheck
            latest_crb = loan.crb_checks.order_by('-checked_at').first()
            if not latest_crb:
                return Response(
                    {'detail': 'CRB check required before loan approval.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            
            # Check for negative listing and require override
            if latest_crb.listed_negative:
                override_reason = serializer.validated_data.get('override_reason', '')
                if not override_reason or len(override_reason) < 10:
                    return Response(
                        {
                            'detail': (
                                'CRB check shows negative listing. '
                                'override_reason (min 10 characters) required.'
                            )
                        },
                        status=status.HTTP_400_BAD_REQUEST,
                    )

        disbursement_payload = None
        with transaction.atomic():
            if new_status == Loan.Status.UNDER_REVIEW:
                loan.status = Loan.Status.UNDER_REVIEW
                if notes:
                    loan.admin_notes = notes
                loan.save(update_fields=['status', 'admin_notes', 'updated_at'])
                audit_action = 'LOAN_UNDER_REVIEW'

            elif new_status == Loan.Status.APPROVED:
                loan.status = Loan.Status.APPROVED
                loan.approved_by = request.user
                if notes:
                    loan.admin_notes = notes
                loan.save(
                    update_fields=[
                        'status',
                        'approved_by',
                        'admin_notes',
                        'updated_at',
                    ],
                )
                persist_loan_repayment_schedule(loan)
                self._notify_member_approved(loan)
                
                # Check if CRB override was used
                if latest_crb and latest_crb.listed_negative:
                    override_reason = serializer.validated_data.get('override_reason', '')
                    audit_action = 'LOAN_APPROVED_WITH_CRB_OVERRIDE'
                else:
                    audit_action = 'LOAN_APPROVED'

            elif new_status == Loan.Status.REJECTED:
                loan.status = Loan.Status.REJECTED
                loan.rejection_reason = notes or loan.rejection_reason
                if notes:
                    loan.admin_notes = notes
                loan.save(
                    update_fields=[
                        'status',
                        'rejection_reason',
                        'admin_notes',
                        'updated_at',
                    ],
                )
                self._notify_member_rejected(loan, notes)
                audit_action = 'LOAN_REJECTED'

            elif new_status == Loan.Status.DISBURSED:
                success, payload, http_status = initiate_loan_disbursement(
                    loan,
                )
                if not success:
                    return Response(payload, status=http_status)
                disbursement_payload = payload
                if notes:
                    loan.admin_notes = notes
                    loan.save(update_fields=['admin_notes', 'updated_at'])
                audit_action = 'LOAN_DISBURSEMENT_INITIATED'
            else:
                raise ValidationError({'status': 'Unsupported status value.'})

        # Include override reason in new_values if CRB override was used
        new_values = {
            'status': loan.status,
            'admin_notes': loan.admin_notes,
            'rejection_reason': loan.rejection_reason,
        }
        if audit_action == 'LOAN_APPROVED_WITH_CRB_OVERRIDE':
            new_values['crb_override_reason'] = override_reason
        
        log_audit(
            request.user,
            audit_action,
            'Loan',
            loan.id,
            old_values=old_values,
            new_values=new_values,
            request=request,
        )

        response_data = {
            'loan_id': str(loan.id),
            'status': loan.status,
            'admin_notes': loan.admin_notes,
            'rejection_reason': loan.rejection_reason,
        }
        if disbursement_payload:
            response_data['disbursement'] = disbursement_payload

        return Response(response_data, status=status.HTTP_200_OK)

    def _validate_transition(self, current_status, new_status):
        if new_status == Loan.Status.UNDER_REVIEW:
            allowed_from = {
                Loan.Status.PENDING_APPROVAL,
                Loan.Status.BOARD_REVIEW,
            }
            if current_status not in allowed_from:
                raise ValidationError(
                    {
                        'status': (
                            f'Cannot move to UNDER_REVIEW from '
                            f'{current_status}.'
                        ),
                    },
                )
            return

        if new_status == Loan.Status.APPROVED:
            if current_status != Loan.Status.UNDER_REVIEW:
                raise ValidationError(
                    {
                        'status': (
                            f'APPROVED is only allowed from UNDER_REVIEW, '
                            f'not {current_status}.'
                        ),
                    },
                )
            return

        if new_status == Loan.Status.REJECTED:
            if current_status in LOAN_FINAL_STATUSES:
                raise ValidationError(
                    {
                        'status': (
                            f'Cannot reject a loan in final status '
                            f'{current_status}.'
                        ),
                    },
                )
            return

        if new_status == Loan.Status.DISBURSED:
            if current_status != Loan.Status.APPROVED:
                raise ValidationError(
                    {
                        'status': (
                            f'DISBURSED is only allowed from APPROVED, '
                            f'not {current_status}.'
                        ),
                    },
                )

    def _notify_member_approved(self, loan):
        create_notification(
            user=loan.membership.user,
            title='Loan approved',
            message=(
                f'Your loan application of KES {loan.amount:,.2f} at '
                f'{loan.membership.sacco.name} has been approved.'
            ),
            category=Notification.Category.LOAN,
            related_object_type='Loan',
            related_object_id=str(loan.id),
            dispatch_async=False,
        )

    def _notify_member_rejected(self, loan, notes):
        reason = notes or 'No reason provided.'
        create_notification(
            user=loan.membership.user,
            title='Loan rejected',
            message=(
                f'Your loan application of KES {loan.amount:,.2f} at '
                f'{loan.membership.sacco.name} was rejected. '
                f'Reason: {reason}'
            ),
            category=Notification.Category.LOAN,
            related_object_type='Loan',
            related_object_id=str(loan.id),
            dispatch_async=False,
        )
