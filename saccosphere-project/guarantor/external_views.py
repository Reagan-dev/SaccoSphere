from datetime import timedelta

from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers
from rest_framework import status
from rest_framework.exceptions import PermissionDenied
from rest_framework.generics import CreateAPIView, ListAPIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsSaccoAdmin
from notifications.models import Notification
from notifications.utils import create_notification
from saccomanagement.models import Role
from services.models import Loan

from .external_serializers import (
    ExternalGuarantorCreateSerializer,
    ExternalGuarantorDetailSerializer,
    ExternalGuarantorResponseSerializer,
)
from .models import ExternalGuarantor
from .utils import generate_response_token, send_guarantor_sms


def get_admin_sacco(request):
    current_sacco = getattr(request, 'current_sacco', None)
    if current_sacco is not None:
        return current_sacco

    sacco_id = request.headers.get('X-Sacco-ID')
    admin_roles = Role.objects.filter(
        user=request.user,
        name=Role.SACCO_ADMIN,
        sacco__isnull=False,
    ).select_related('sacco')

    if sacco_id:
        role = admin_roles.filter(sacco_id=sacco_id).first()
    else:
        role = admin_roles.first()

    if role is None:
        raise PermissionDenied(
            'You do not have permission to manage this SACCO.'
        )

    return role.sacco


class ExternalGuarantorAdminReviewSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=('APPROVE', 'REJECT'))
    admin_notes = serializers.CharField(
        max_length=500,
        required=False,
        allow_blank=True,
    )


class ExternalGuarantorCreateView(CreateAPIView):
    serializer_class = ExternalGuarantorCreateSerializer
    permission_classes = [IsAuthenticated]

    def get_serializer(self, *args, **kwargs):
        data = kwargs.get('data')
        if data is not None:
            copied_data = data.copy()
            copied_data['loan_id'] = self.kwargs['loan_id']
            kwargs['data'] = copied_data
        return super().get_serializer(*args, **kwargs)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        external_guarantor = serializer.save(
            response_token=generate_response_token(),
            response_token_expires_at=timezone.now() + timedelta(hours=48),
            status=ExternalGuarantor.Status.PENDING_SMS,
        )

        sms_sent = send_guarantor_sms(external_guarantor)
        if sms_sent:
            external_guarantor.status = ExternalGuarantor.Status.SMS_SENT
            external_guarantor.save(update_fields=['status', 'updated_at'])
            self._notify_applicant_sms_sent(external_guarantor)
            external_guarantor.refresh_from_db()

        response_serializer = ExternalGuarantorDetailSerializer(
            external_guarantor,
            context=self.get_serializer_context(),
        )
        return Response(
            response_serializer.data,
            status=status.HTTP_201_CREATED,
        )

    def _notify_applicant_sms_sent(self, external_guarantor):
        create_notification(
            user=external_guarantor.requested_by,
            title='Guarantor SMS Sent',
            message=(
                f'{external_guarantor.full_name} has been sent an SMS to '
                'approve your guarantee request.'
            ),
            category=Notification.Category.GUARANTOR,
            related_object_type='ExternalGuarantor',
            related_object_id=str(external_guarantor.id),
            dispatch_async=False,
        )


class ExternalGuarantorListView(ListAPIView):
    serializer_class = ExternalGuarantorDetailSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        loan = self._get_authorized_loan()
        return ExternalGuarantor.objects.filter(loan=loan).select_related(
            'loan',
            'requested_by',
            'sacco',
            'reviewed_by',
        )

    def _get_authorized_loan(self):
        loan = get_object_or_404(
            Loan.objects.select_related(
                'membership',
                'membership__user',
                'membership__sacco',
            ),
            id=self.kwargs['loan_id'],
        )

        user = self.request.user
        if loan.membership.user == user:
            return loan

        is_admin = Role.objects.filter(
            user=user,
            sacco=loan.membership.sacco,
            name=Role.SACCO_ADMIN,
        ).exists()
        is_super_admin = Role.objects.filter(
            user=user,
            name=Role.SUPER_ADMIN,
        ).exists()

        if is_admin or is_super_admin:
            return loan

        raise PermissionDenied(
            'You do not have permission to view these guarantors.'
        )


class ExternalGuarantorCollectionView(
    ExternalGuarantorCreateView,
    ExternalGuarantorListView,
):
    def get_serializer_class(self):
        if self.request.method == 'POST':
            return ExternalGuarantorCreateSerializer
        return ExternalGuarantorDetailSerializer

    def get(self, request, *args, **kwargs):
        return ExternalGuarantorListView.get(self, request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        return ExternalGuarantorCreateView.post(
            self,
            request,
            *args,
            **kwargs,
        )


class ExternalGuarantorRespondView(APIView):
    permission_classes = [AllowAny]

    def post(self, request, response_token):
        serializer = ExternalGuarantorResponseSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        external_guarantor = self._get_external_guarantor(response_token)
        action = serializer.validated_data['action']
        notes = serializer.validated_data.get('notes', '')

        if action == 'ACCEPT':
            self._accept(external_guarantor, notes)
        else:
            self._decline(external_guarantor, notes)

        return Response(
            {'message': 'Thank you. Your response has been recorded.'},
            status=status.HTTP_200_OK,
        )

    def _get_external_guarantor(self, response_token):
        try:
            external_guarantor = ExternalGuarantor.objects.select_related(
                'loan',
                'requested_by',
            ).get(response_token=response_token)
        except ExternalGuarantor.DoesNotExist:
            raise PermissionDenied('Invalid guarantor response token.')

        if external_guarantor.response_token_expires_at <= timezone.now():
            raise PermissionDenied('Guarantor response token has expired.')

        if external_guarantor.status != ExternalGuarantor.Status.SMS_SENT:
            raise PermissionDenied(
                'This guarantor request has already been responded to.'
            )

        return external_guarantor

    def _accept(self, external_guarantor, notes):
        external_guarantor.status = ExternalGuarantor.Status.ACCEPTED
        external_guarantor.guarantor_response = (
            ExternalGuarantor.GuarantorResponse.ACCEPTED
        )
        external_guarantor.guarantor_response_notes = notes
        external_guarantor.guarantor_responded_at = timezone.now()
        external_guarantor.save(update_fields=[
            'status',
            'guarantor_response',
            'guarantor_response_notes',
            'guarantor_responded_at',
            'updated_at',
        ])
        self._notify_applicant(
            external_guarantor,
            (
                f'{external_guarantor.full_name} has accepted your guarantee '
                'request. It is now under admin review.'
            ),
        )

    def _decline(self, external_guarantor, notes):
        external_guarantor.status = ExternalGuarantor.Status.DECLINED
        external_guarantor.guarantor_response = (
            ExternalGuarantor.GuarantorResponse.DECLINED
        )
        external_guarantor.guarantor_response_notes = notes
        external_guarantor.guarantor_responded_at = timezone.now()
        external_guarantor.save(update_fields=[
            'status',
            'guarantor_response',
            'guarantor_response_notes',
            'guarantor_responded_at',
            'updated_at',
        ])
        self._notify_applicant(
            external_guarantor,
            (
                f'{external_guarantor.full_name} has declined your guarantee '
                'request.'
            ),
        )

    def _notify_applicant(self, external_guarantor, message):
        create_notification(
            user=external_guarantor.requested_by,
            title='External Guarantor Response',
            message=message,
            category=Notification.Category.GUARANTOR,
            related_object_type='ExternalGuarantor',
            related_object_id=str(external_guarantor.id),
            dispatch_async=False,
        )


class ExternalGuarantorAdminListView(ListAPIView):
    serializer_class = ExternalGuarantorDetailSerializer
    permission_classes = [IsAuthenticated, IsSaccoAdmin]

    def get_queryset(self):
        queryset = ExternalGuarantor.objects.filter(
            sacco=get_admin_sacco(self.request),
        ).select_related(
            'loan',
            'requested_by',
            'sacco',
            'reviewed_by',
        )
        status_filter = self.request.query_params.get('status')

        if status_filter:
            queryset = queryset.filter(status=status_filter)

        return queryset


class ExternalGuarantorAdminReviewView(APIView):
    permission_classes = [IsAuthenticated, IsSaccoAdmin]

    def patch(self, request, pk):
        serializer = ExternalGuarantorAdminReviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        external_guarantor = get_object_or_404(
            ExternalGuarantor.objects.select_related(
                'loan',
                'requested_by',
                'sacco',
                'reviewed_by',
            ),
            pk=pk,
            sacco=get_admin_sacco(request),
        )
        action = serializer.validated_data['action']
        admin_notes = serializer.validated_data.get('admin_notes', '')

        if (
            action == 'APPROVE'
            and external_guarantor.status != ExternalGuarantor.Status.ACCEPTED
        ):
            return Response(
                {
                    'detail': (
                        'Only accepted external guarantors can be approved.'
                    ),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if action == 'APPROVE':
            self._approve(external_guarantor, admin_notes, request.user)
        else:
            self._reject(external_guarantor, admin_notes, request.user)

        external_guarantor.refresh_from_db()
        response_serializer = ExternalGuarantorDetailSerializer(
            external_guarantor,
            context={'request': request},
        )
        return Response(response_serializer.data, status=status.HTTP_200_OK)

    def _approve(self, external_guarantor, admin_notes, user):
        external_guarantor.status = (
            ExternalGuarantor.Status.APPROVED_BY_ADMIN
        )
        external_guarantor.reviewed_by = user
        external_guarantor.reviewed_at = timezone.now()
        external_guarantor.admin_notes = admin_notes
        external_guarantor.save(update_fields=[
            'status',
            'reviewed_by',
            'reviewed_at',
            'admin_notes',
            'updated_at',
        ])
        self._notify_applicant(
            external_guarantor,
            (
                f'{external_guarantor.full_name}\'s guarantee has been '
                f'approved by {external_guarantor.sacco.name}. '
                'Your loan application can now proceed.'
            ),
        )

    def _reject(self, external_guarantor, admin_notes, user):
        external_guarantor.status = (
            ExternalGuarantor.Status.REJECTED_BY_ADMIN
        )
        external_guarantor.reviewed_by = user
        external_guarantor.reviewed_at = timezone.now()
        external_guarantor.admin_notes = admin_notes
        external_guarantor.save(update_fields=[
            'status',
            'reviewed_by',
            'reviewed_at',
            'admin_notes',
            'updated_at',
        ])
        self._notify_applicant(
            external_guarantor,
            (
                f'{external_guarantor.full_name}\'s guarantee was not '
                'approved. Please add another guarantor.'
            ),
        )

    def _notify_applicant(self, external_guarantor, message):
        create_notification(
            user=external_guarantor.requested_by,
            title='External Guarantor Review',
            message=message,
            category=Notification.Category.GUARANTOR,
            related_object_type='ExternalGuarantor',
            related_object_id=str(external_guarantor.id),
            dispatch_async=False,
        )
