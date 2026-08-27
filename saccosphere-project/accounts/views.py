import hashlib
import logging
import time

from config.utils import sanitize_pii
from django.contrib.auth import authenticate
from django.core.exceptions import FieldError
from django.db.models import Count, Q
from django.utils import timezone
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework import status
from rest_framework.generics import (
    CreateAPIView,
    ListAPIView,
    RetrieveAPIView,
    RetrieveUpdateAPIView,
    UpdateAPIView,
)
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from config.response import StandardResponseMixin
from django.db import IntegrityError, transaction
from saccomanagement.audit_logger import log_audit
from saccomanagement.odpc_logging import DataAccessMixin

from .integrations.iprs_client import IPRSClient, IPRSError

try:
    from accounts.kyc_metrics import (
        increment_kyc_submission,
        observe_processing_time,
    )
except ImportError:
    # Metrics module not available (e.g., during initial migration)
    increment_kyc_submission = None
    observe_processing_time = None
from .models import (
    DataErasureRequest,
    KYCVerification,
    Sacco,
    User,
    PasswordResetToken,
)


# LOCKING ORDER (to avoid deadlocks):
# 1. KYCVerification row (select_for_update)
# 2. SystemAuditLog row (created by log_audit)
# Never acquire locks in reverse order. If you need to lock multiple tables,
# always acquire KYCVerification lock first, then any other locks.
from . import serializers as account_serializers
from .permissions import IsSaccoAdminOrSuperAdmin
from .utils import get_user_sacco_context
from .serializers import (
    AdminKYCReviewSerializer,
    KYCStatusSerializer,
    KYCUploadSerializer,
    PasswordChangeSerializer,
    PasswordResetConfirmSerializer,
    SaccoDetailSerializer,
    SaccoListSerializer,
    UserLoginSerializer,
    UserProfileSerializer,
    UserRegistrationSerializer,
)
from .otp_utils import create_otp_token, verify_otp, OTPError, format_phone_number
from .otp_backends import get_otp_backend, OTPDeliveryError
from .throttles import OTPSendThrottle, OTPSendIPThrottle, OTPVerifyThrottle


def _mask_contact(channel, destination):
    """
    Mask contact information for API responses.

    Args:
        channel: 'PHONE' or 'EMAIL'
        destination: The phone number or email address to mask

    Returns:
        str: Masked contact information
    """
    if channel == 'EMAIL':
        # Mask email: j***@example.com
        if '@' not in destination:
            return destination
        local, domain = destination.split('@', 1)
        if len(local) > 1:
            masked_local = local[0] + '*' * (len(local) - 1)
        else:
            masked_local = local
        return f'{masked_local}@{domain}'
    elif channel == 'PHONE':
        # Mask phone: +254*****23
        if len(destination) < 4:
            return destination
        # Keep country code and last 2 digits
        if destination.startswith('+'):
            country_code = destination[:4]  # +254
            last_digits = destination[-2:]
            middle = '*' * (len(destination) - len(country_code) - len(last_digits))
            return f'{country_code}{middle}{last_digits}'
        else:
            last_digits = destination[-2:]
            middle = '*' * (len(destination) - len(last_digits))
            return f'{middle}{last_digits}'
    return destination


class PublicStatsView(APIView):
    """Return public platform counters used by the frontend."""

    permission_classes = [AllowAny]

    def get(self, request):
        from saccomembership.models import Membership

        total_saccos = Sacco.objects.filter(
            is_active=True,
            is_publicly_listed=True,
        ).count()
        total_members_on_app = Membership.objects.filter(
            status=Membership.Status.APPROVED,
        ).count()

        return Response(
            {
                'total_saccos': total_saccos,
                'total_members_on_app': total_members_on_app,
            },
            status=status.HTTP_200_OK,
        )


def get_user_by_phone_number(phone_number):
    """Return the newest user for a phone number without failing on duplicates."""
    from .otp_utils import format_phone_number
    formatted_phone = format_phone_number(phone_number)
    return (
        User.objects.filter(phone_number=formatted_phone)
        .order_by('-date_joined')
        .first()
    )


def apply_iprs_result(kyc, result, request=None, correlation_id=None):
    """Save IPRS verification details on a KYC record."""
    outcome = result.get('outcome')
    if not outcome and result.get('verified'):
        outcome = 'verified'

    kyc.id_number = result.get('id_number') or kyc.id_number
    kyc.iprs_verified = outcome == 'verified'
    kyc.iprs_reference = result.get('iprs_reference') or ''
    kyc.iprs_attempted_at = timezone.now()
    kyc.iprs_error = result.get('error') or ''

    if outcome == 'mismatch':
        kyc.status = KYCVerification.Status.IPRS_MISMATCH
    elif outcome == 'rejected_by_iprs':
        kyc.status = KYCVerification.Status.IPRS_REJECTED
        if increment_kyc_submission:
            increment_kyc_submission('rejected')
    elif outcome == 'iprs_unavailable':
        kyc.status = KYCVerification.Status.IPRS_UNAVAILABLE
        if increment_kyc_submission:
            increment_kyc_submission('iprs_unavailable')
    elif outcome == 'unavailable':
        # Backward compatibility: map old unavailable to iprs_unavailable
        kyc.status = KYCVerification.Status.IPRS_UNAVAILABLE
        if increment_kyc_submission:
            increment_kyc_submission('iprs_unavailable')
    elif outcome == 'verified' and kyc.status in {
        KYCVerification.Status.IPRS_MISMATCH,
        KYCVerification.Status.PENDING_MANUAL,
        KYCVerification.Status.IPRS_REJECTED,
        KYCVerification.Status.IPRS_UNAVAILABLE,
    }:
        if kyc.id_front and kyc.id_back:
            kyc.status = KYCVerification.Status.PENDING
            if increment_kyc_submission:
                increment_kyc_submission('approved')
        else:
            kyc.status = KYCVerification.Status.NOT_STARTED

    try:
        with transaction.atomic():
            kyc.save(
                update_fields=[
                    'id_number',
                    'normalized_id_number',
                    'iprs_verified',
                    'iprs_reference',
                    'iprs_attempted_at',
                    'iprs_error',
                    'status',
                ],
            )
    except IntegrityError:
        # Log the duplicate ID error with structured context
        logger.warning(
            'Duplicate ID number detected',
            extra={
                'correlation_id': correlation_id or '-',
                'kyc_submission_id': str(kyc.id),
                'id_number_ref': sanitize_pii(kyc.id_number),
                'step': 'apply_iprs_result',
                'error_type': 'integrity_error',
                'outcome': 'duplicate_id',
            },
        )
        # Try to log to audit table in a separate transaction
        try:
            with transaction.atomic(savepoint=False):
                log_audit(
                    user=kyc.user,
                    action='DUPLICATE_ID_ATTEMPT',
                    resource_type='KYCVerification',
                    resource_id=kyc.id,
                    old_values={},
                    new_values={'id_number': '[REDACTED]'},  # Never log raw PII
                    request=request,
                )
        except Exception:
            # Audit log failed, but we already logged to the standard logger
            pass
        raise


class RegisterView(StandardResponseMixin, CreateAPIView):
    serializer_class = UserRegistrationSerializer
    permission_classes = [AllowAny]

    @swagger_auto_schema(
        operation_summary='Register a new user',
        request_body=UserRegistrationSerializer,
        responses={201: UserProfileSerializer},
    )
    def post(self, request, *args, **kwargs):
        return self.create(request, *args, **kwargs)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        data = UserProfileSerializer(
            user,
            context=self.get_serializer_context(),
        ).data
        return self.created(data, 'User registered successfully')


class LoginView(StandardResponseMixin, APIView):
    permission_classes = [AllowAny]

    @swagger_auto_schema(
        operation_summary='Log in with email and password',
        request_body=UserLoginSerializer,
        responses={200: openapi.Response('Login successful')},
    )
    def post(self, request):
        serializer = UserLoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        email = serializer.validated_data['email']
        password = serializer.validated_data['password']
        user = authenticate(
            request,
            username=email,
            password=password,
        )

        if user is None:
            return Response(
                {
                    'success': False,
                    'message': 'Invalid email or password',
                    'errors': None,
                    'error_code': 'UNAUTHORIZED',
                    'status_code': status.HTTP_401_UNAUTHORIZED,
                },
                status=status.HTTP_401_UNAUTHORIZED,
            )

        refresh = RefreshToken.for_user(user)
        sacco_context = get_user_sacco_context(user)
        data = {
            'access': str(refresh.access_token),
            'refresh': str(refresh),
            'user': UserProfileSerializer(user).data,
            'sacco_context': sacco_context,
            'sacco_id': sacco_context['sacco_id'],
        }
        return self.ok(data, 'Login successful')


class LogoutView(StandardResponseMixin, APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary='Log out and blacklist refresh token',
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=['refresh'],
            properties={
                'refresh': openapi.Schema(type=openapi.TYPE_STRING),
            },
        ),
        responses={200: openapi.Response('Logged out successfully')},
    )
    def post(self, request):
        refresh_token = request.data.get('refresh')

        if not refresh_token:
            return self.bad_request(
                'Refresh token is required',
                {'refresh': 'This field is required.'},
            )

        try:
            token = RefreshToken(refresh_token)
            token.blacklist()
        except Exception:
            return self.bad_request(
                'Invalid refresh token',
                {'refresh': 'Token is invalid or already blacklisted.'},
            )

        return self.ok(None, 'Logged out successfully')


class MeView(StandardResponseMixin, RetrieveUpdateAPIView):
    serializer_class = UserProfileSerializer
    permission_classes = [IsAuthenticated]
    http_method_names = ['get', 'patch', 'head', 'options']

    def get_object(self):
        return self.request.user

    @swagger_auto_schema(
        operation_summary='Get authenticated user profile',
        responses={200: UserProfileSerializer},
    )
    def get(self, request, *args, **kwargs):
        serializer = self.get_serializer(self.get_object())
        return self.ok(serializer.data)

    @swagger_auto_schema(
        operation_summary='Update authenticated user profile',
        request_body=UserProfileSerializer,
        responses={200: UserProfileSerializer},
    )
    def patch(self, request, *args, **kwargs):
        serializer = self.get_serializer(
            self.get_object(),
            data=request.data,
            partial=True,
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return self.ok(serializer.data, 'Profile updated successfully')


class PasswordChangeView(StandardResponseMixin, APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary='Change authenticated user password',
        request_body=PasswordChangeSerializer,
        responses={200: openapi.Response('Password changed successfully')},
    )
    def post(self, request):
        serializer = PasswordChangeSerializer(
            data=request.data,
            context={'request': request},
        )
        serializer.is_valid(raise_exception=True)

        request.user.set_password(serializer.validated_data['new_password'])
        request.user.save(update_fields=['password'])
        return self.ok(None, 'Password changed successfully')


class KYCUploadView(APIView):
    """Upload KYC documents for the authenticated user."""

    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    @swagger_auto_schema(
        operation_description='Upload a KYC document for the authenticated user.',
        request_body=KYCUploadSerializer,
        responses={200: KYCStatusSerializer, 400: 'Bad Request', 401: 'Unauthorized'},
        security=[{'Bearer': []}],
    )
    def post(self, request):
        """Validate and save a KYC document upload with proper locking."""
        start_time = time.time()
        serializer = KYCUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        document_type = serializer.validated_data['document_type']
        uploaded_file = serializer.validated_data['file']
        correlation_id = getattr(request, 'correlation_id', None)

        # Calculate file hash for idempotency
        uploaded_file.seek(0)
        file_hash = hashlib.sha256(uploaded_file.read()).hexdigest()
        uploaded_file.seek(0)

        with transaction.atomic():
            # Lock the KYC row to prevent concurrent uploads from overwriting each other
            kyc, created = KYCVerification.objects.select_for_update().get_or_create(
                user=request.user,
                defaults={'status': KYCVerification.Status.NOT_STARTED},
            )

            # Idempotency check: if the same file is already uploaded for this side, skip
            current_file = getattr(kyc, document_type)
            if current_file:
                try:
                    current_file.seek(0)
                    current_hash = hashlib.sha256(current_file.read()).hexdigest()
                    current_file.seek(0)
                    if current_hash == file_hash:
                        # Same file already uploaded - return success without changes
                        response_serializer = KYCStatusSerializer(
                            kyc,
                            context={'request': request},
                        )
                        return Response(
                            response_serializer.data,
                            status=status.HTTP_200_OK,
                        )
                except (IOError, OSError):
                    # If we can't read the current file, proceed with upload
                    pass

            # Update the document field
            setattr(kyc, document_type, uploaded_file)
            update_fields = [document_type]

            # Check if both sides are now uploaded
            if kyc.id_front and kyc.id_back:
                kyc.status = KYCVerification.Status.PENDING
                kyc.submitted_at = timezone.now()
                kyc.rejection_reason = ''
                update_fields.extend([
                    'status',
                    'submitted_at',
                    'rejection_reason',
                ])
                
                # Record submission metric
                if increment_kyc_submission:
                    increment_kyc_submission('submitted')

            kyc.save(update_fields=update_fields)

        # IPRS verification happens outside the transaction to avoid holding locks
        # during network calls. We re-check the locked state before calling IPRS.
        self._auto_verify_id(kyc, correlation_id=correlation_id)

        # Record processing time
        duration = time.time() - start_time
        if observe_processing_time:
            observe_processing_time(duration)

        response_serializer = KYCStatusSerializer(
            kyc,
            context={'request': request},
        )
        return Response(
            response_serializer.data,
            status=status.HTTP_200_OK,
        )

    def _auto_verify_id(self, kyc, correlation_id=None):
        """Run IPRS verification without holding database locks during the network call."""
        # Only call IPRS if we have an id_number
        if not kyc.id_number:
            return

        # First, call IPRS without holding any lock
        try:
            result = IPRSClient().verify_id(
                kyc.id_number,
                date_of_birth=kyc.user.date_of_birth,
                full_name=kyc.user.get_full_name(),
                correlation_id=correlation_id,
                kyc_submission_id=str(kyc.id),
            )
        except IPRSError:
            result = {
                'outcome': 'unavailable',
                'verified': False,
                'id_number': kyc.id_number,
                'iprs_reference': '',
                'error': 'IPRS request failed.',
            }

        # Now open a short transaction to apply the result with proper locking
        with transaction.atomic():
            # Re-fetch the KYC record with a lock to ensure we're acting on fresh state
            fresh_kyc = KYCVerification.objects.select_for_update().get(pk=kyc.pk)

            # Only apply IPRS result if the record still has both sides AND id_number
            # This prevents applying stale results if the record was modified during the IPRS call
            if (
                fresh_kyc.id_front
                and fresh_kyc.id_back
                and fresh_kyc.id_number
                and fresh_kyc.status == KYCVerification.Status.PENDING
            ):
                try:
                    apply_iprs_result(
                        fresh_kyc, result, request=None, correlation_id=correlation_id
                    )
                except IntegrityError:
                    # Log the error but don't raise - the upload already succeeded
                    logger.warning(
                        'IntegrityError applying IPRS result for KYC %s after upload',
                        fresh_kyc.id,
                        extra={
                            'correlation_id': correlation_id or '-',
                            'kyc_submission_id': str(fresh_kyc.id),
                            'step': 'apply_iprs_result',
                            'error_type': 'integrity_error',
                        },
                    )


class KYCSubmitIDView(APIView):
    """Submit a national ID number for IPRS verification."""

    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_description='Submit national ID details for IPRS verification.',
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=['id_number'],
            properties={
                'id_number': openapi.Schema(type=openapi.TYPE_STRING),
                'date_of_birth': openapi.Schema(type=openapi.TYPE_STRING),
            },
        ),
        responses={200: openapi.Response('OK'), 400: 'Bad Request', 401: 'Unauthorized'},
        security=[{'Bearer': []}],
    )
    def post(self, request):
        id_number = request.data.get('id_number')
        date_of_birth = request.data.get('date_of_birth')
        correlation_id = getattr(request, 'correlation_id', None)

        if not id_number:
            return Response(
                {'id_number': 'This field is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Call IPRS without holding any database lock
        try:
            result = IPRSClient().verify_id(
                id_number,
                date_of_birth=date_of_birth,
                full_name=request.user.get_full_name(),
                correlation_id=correlation_id,
            )
        except IPRSError:
            result = {
                'outcome': 'unavailable',
                'verified': False,
                'id_number': id_number,
                'iprs_reference': '',
                'error': 'IPRS request failed.',
            }

        # Apply the result with proper locking
        with transaction.atomic():
            kyc, created = KYCVerification.objects.select_for_update().get_or_create(
                user=request.user,
                defaults={'status': KYCVerification.Status.NOT_STARTED},
            )

            try:
                apply_iprs_result(kyc, result, request=request, correlation_id=correlation_id)
            except IntegrityError:
                return Response(
                    {
                        'detail': (
                            'Unable to process your KYC verification. '
                            'Please contact support if this issue persists.'
                        )
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

        return Response(
            {
                'outcome': result.get('outcome'),
                'iprs_verified': kyc.iprs_verified,
                'status': kyc.status,
                'id_number': kyc.id_number,
                'name': result.get('name'),
                'iprs_reference': kyc.iprs_reference,
                'error': result.get('error'),
            },
            status=status.HTTP_200_OK,
        )


class KYCStatusView(RetrieveAPIView):
    """Return the authenticated user's KYC status."""

    serializer_class = KYCStatusSerializer
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_description="Get the authenticated user's KYC status.",
        responses={200: KYCStatusSerializer, 400: 'Bad Request', 401: 'Unauthorized'},
        security=[{'Bearer': []}],
    )
    def get(self, request, *args, **kwargs):
        return self.retrieve(request, *args, **kwargs)

    def get_object(self):
        """Get or create the user's KYC verification record."""
        kyc, _ = KYCVerification.objects.get_or_create(
            user=self.request.user,
            defaults={'status': KYCVerification.Status.NOT_STARTED},
        )
        return kyc


class AdminKYCQuerysetMixin:
    """Scope KYC records for super admins and SACCO admins."""

    def get_queryset(self):
        """Return KYC records visible to the current admin."""
        queryset = KYCVerification.objects.select_related(
            'user',
            'reviewed_by',
        )
        user = self.request.user

        if user.roles.filter(name='SUPER_ADMIN').exists():
            return queryset

        admin_sacco_ids = user.roles.filter(
            name='SACCO_ADMIN',
            sacco__isnull=False,
        ).values_list('sacco_id', flat=True)

        return queryset.filter(
            user__membership__sacco_id__in=admin_sacco_ids,
        ).distinct()


class AdminKYCReviewView(AdminKYCQuerysetMixin, UpdateAPIView):
    """Approve or reject a member KYC verification."""

    serializer_class = AdminKYCReviewSerializer
    permission_classes = [IsSaccoAdminOrSuperAdmin]
    lookup_field = 'id'
    lookup_url_kwarg = 'kyc_id'

    @swagger_auto_schema(
        operation_description='Review and approve or reject a member KYC record.',
        request_body=AdminKYCReviewSerializer,
        responses={200: KYCStatusSerializer, 400: 'Bad Request', 401: 'Unauthorized'},
        security=[{'Bearer': []}],
    )
    def patch(self, request, *args, **kwargs):
        """Partially update a KYC review decision."""
        return self.partial_update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        """Apply the admin review decision and notify the member."""
        kyc = self.get_object()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        review_status = serializer.validated_data['status']
        rejection_reason = serializer.validated_data.get(
            'rejection_reason',
            '',
        )
        manual_verification_reason = serializer.validated_data.get(
            'manual_verification_reason',
            '',
        )
        previous_status = kyc.status
        old_values = {
            'status': previous_status,
            'iprs_verified': kyc.iprs_verified,
            'iprs_error': kyc.iprs_error,
            'manual_verification_reason': kyc.manual_verification_reason,
        }

        requires_manual_reason = previous_status in {
            KYCVerification.Status.IPRS_MISMATCH,
            KYCVerification.Status.PENDING_MANUAL,
        }
        if (
            review_status == KYCVerification.Status.APPROVED
            and requires_manual_reason
            and not manual_verification_reason
        ):
            return Response(
                {
                    'manual_verification_reason': (
                        'Manual verification reason is required when '
                        'approving KYC after IPRS did not clear.'
                    ),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        kyc.status = review_status
        kyc.rejection_reason = rejection_reason
        kyc.reviewed_by = request.user

        update_fields = ['status', 'rejection_reason', 'reviewed_by']
        if review_status == KYCVerification.Status.APPROVED:
            kyc.verified_at = timezone.now()
            update_fields.append('verified_at')
            if manual_verification_reason:
                kyc.manual_verification_reason = manual_verification_reason
                update_fields.append('manual_verification_reason')

        kyc.save(update_fields=update_fields)
        if review_status == KYCVerification.Status.APPROVED:
            log_audit(
                user=request.user,
                action='APPROVE_KYC',
                resource_type='KYCVerification',
                resource_id=kyc.id,
                old_values=old_values,
                new_values={
                    'status': kyc.status,
                    'reviewed_by': str(request.user.id),
                    'manual_verification_reason': (
                        kyc.manual_verification_reason
                    ),
                    'iprs_error': kyc.iprs_error,
                },
                request=request,
            )

        self._notify_member(kyc)

        response_serializer = KYCStatusSerializer(
            kyc,
            context={'request': request},
        )
        return Response(
            response_serializer.data,
            status=status.HTTP_200_OK,
        )

    def _notify_member(self, kyc):
        """Notify a member after their KYC review is completed."""
        try:
            from notifications.utils import create_notification
        except ImportError:
            return

        if kyc.status == KYCVerification.Status.APPROVED:
            title = 'KYC approved'
            message = 'Your KYC verification has been approved.'
        else:
            title = 'KYC rejected'
            message = 'Your KYC verification was rejected.'

        create_notification(
            user=kyc.user,
            title=title,
            message=message,
        )


class AdminKYCQueueView(DataAccessMixin, AdminKYCQuerysetMixin, ListAPIView):
    """List pending KYC verification records for admin review."""

    serializer_class = KYCStatusSerializer
    permission_classes = [IsSaccoAdminOrSuperAdmin]
    data_access_type = 'KYC_DOCUMENTS'
    data_access_reason = 'Admin KYC queue review'

    @swagger_auto_schema(
        operation_description='List KYC records pending admin review.',
        responses={200: KYCStatusSerializer(many=True), 400: 'Bad Request', 401: 'Unauthorized'},
        security=[{'Bearer': []}],
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        """Return filtered KYC records visible to the current admin."""
        queryset = super().get_queryset()
        review_status = self.request.query_params.get('status')
        search = self.request.query_params.get('search')

        if review_status:
            queryset = queryset.filter(status=review_status)
        else:
            queryset = queryset.filter(
                status__in=[
                    KYCVerification.Status.PENDING,
                    KYCVerification.Status.IPRS_MISMATCH,
                    KYCVerification.Status.PENDING_MANUAL,
                ]
            )

        if search:
            queryset = queryset.filter(
                Q(user__email__icontains=search)
                | Q(id_number__icontains=search)
            )

        return queryset.order_by('-submitted_at', '-created_at')


class SaccoListView(StandardResponseMixin, ListAPIView):
    serializer_class = SaccoListSerializer
    permission_classes = [AllowAny]

    def get_queryset(self):
        queryset = Sacco.objects.filter(is_active=True)
        user = self.request.user

        if not user.is_authenticated or not user.is_staff:
            queryset = queryset.filter(is_publicly_listed=True)

        # Annotate member count
        queryset = queryset.annotate(
            member_count=Count(
                'membership',
                filter=Q(membership__status='APPROVED'),
            )
        )

        # Search filters
        search = self.request.query_params.get('search')
        if search:
            queryset = queryset.filter(
                Q(name__icontains=search)
                | Q(description__icontains=search)
                | Q(registration_number__icontains=search)
            )

        # Exact filters
        sector = self.request.query_params.get('sector')
        if sector:
            queryset = queryset.filter(sector=sector)

        county = self.request.query_params.get('county')
        if county:
            queryset = queryset.filter(county__icontains=county)

        membership_type = self.request.query_params.get('membership_type')
        if membership_type:
            queryset = queryset.filter(membership_type=membership_type)

        # Boolean filters
        verified_only = self.request.query_params.get('verified_only')
        if verified_only == 'true':
            queryset = queryset.filter(is_verified=True)

        # Member count range filters
        min_members = self.request.query_params.get('min_members')
        if min_members:
            try:
                min_members = int(min_members)
                queryset = queryset.filter(member_count__gte=min_members)
            except (ValueError, TypeError):
                pass

        max_members = self.request.query_params.get('max_members')
        if max_members:
            try:
                max_members = int(max_members)
                queryset = queryset.filter(member_count__lte=max_members)
            except (ValueError, TypeError):
                pass

        # Ordering
        ordering = self.request.query_params.get('ordering', '-created_at')
        valid_orderings = [
            'name',
            '-name',
            'member_count',
            '-member_count',
            'created_at',
            '-created_at',
        ]
        if ordering in valid_orderings:
            queryset = queryset.order_by(ordering)

        return queryset

    @swagger_auto_schema(
        operation_summary='List SACCOs',
        manual_parameters=[
            openapi.Parameter(
                'search',
                openapi.IN_QUERY,
                type=openapi.TYPE_STRING,
                description='Search SACCO name or description.',
            ),
            openapi.Parameter(
                'sector',
                openapi.IN_QUERY,
                type=openapi.TYPE_STRING,
                description='Filter by SACCO sector.',
            ),
            openapi.Parameter(
                'county',
                openapi.IN_QUERY,
                type=openapi.TYPE_STRING,
                description='Filter by Kenya county.',
            ),
            openapi.Parameter(
                'membership_type',
                openapi.IN_QUERY,
                type=openapi.TYPE_STRING,
                description='Filter by membership type.',
            ),
            openapi.Parameter(
                'verified_only',
                openapi.IN_QUERY,
                type=openapi.TYPE_BOOLEAN,
                description='Only return verified SACCOs when true.',
            ),
            openapi.Parameter(
                'min_members',
                openapi.IN_QUERY,
                type=openapi.TYPE_INTEGER,
                description='Minimum number of approved members.',
            ),
            openapi.Parameter(
                'max_members',
                openapi.IN_QUERY,
                type=openapi.TYPE_INTEGER,
                description='Maximum number of approved members.',
            ),
            openapi.Parameter(
                'ordering',
                openapi.IN_QUERY,
                type=openapi.TYPE_STRING,
                description='Order results by field. Use - prefix for descending.',
            ),
        ],
        responses={200: SaccoListSerializer(many=True)},
    )
    def get(self, request, *args, **kwargs):
        return self.list(request, *args, **kwargs)

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)

        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = self.get_serializer(queryset, many=True)
        return self.ok(serializer.data)


class SaccoDetailView(StandardResponseMixin, RetrieveAPIView):
    serializer_class = SaccoDetailSerializer
    permission_classes = [AllowAny]
    lookup_field = 'id'

    def get_queryset(self):
        queryset = Sacco.objects.filter(is_active=True)
        user = self.request.user

        if not user.is_authenticated or not user.is_staff:
            queryset = queryset.filter(is_publicly_listed=True)

        try:
            return queryset.annotate(
                member_count=Count(
                    'membership',
                    filter=Q(membership__status='approved'),
                )
            )
        except FieldError:
            return queryset

    @swagger_auto_schema(
        operation_summary='Get SACCO details',
        responses={200: SaccoDetailSerializer},
    )
    def get(self, request, *args, **kwargs):
        return self.retrieve(request, *args, **kwargs)

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance)
        return self.ok(serializer.data)


class OTPSendView(APIView):
    """Send OTP to user's phone number or email."""
    permission_classes = [AllowAny]
    throttle_classes = [OTPSendThrottle, OTPSendIPThrottle]

    @swagger_auto_schema(
        operation_summary='Send OTP code',
        request_body=account_serializers.OTPRequestSerializer,
        responses={200: {'type': 'object', 'properties': {'message': {'type': 'string'}}}},
    )
    def post(self, request):
        serializer = account_serializers.OTPRequestSerializer(
            data=request.data,
            context={'request': request}
        )
        serializer.is_valid(raise_exception=True)

        phone_number = serializer.validated_data['phone_number']
        purpose = serializer.validated_data['purpose']
        channel = serializer.validated_data['channel']
        formatted_phone = format_phone_number(phone_number)

        # Find user if purpose requires it
        user = None
        if purpose in ['PASSWORD_RESET', 'LOGIN']:
            user = get_user_by_phone_number(phone_number)
            if user is None:
                # For password reset and login, don't reveal if phone exists
                return Response({'message': 'OTP sent. Check your phone.'}, status=200)
        elif purpose == 'PHONE_VERIFY':
            # For registration, allow any phone number
            user = get_user_by_phone_number(phone_number)

        try:
            # Create OTP token
            token = create_otp_token(user, formatted_phone, purpose)
            # Save channel
            token.channel = channel
            token.save(update_fields=['channel'])

            # Get backend and send
            backend = get_otp_backend(channel)
            backend.send(token)

            # Determine destination for response
            if channel == 'EMAIL':
                destination = user.email if user else None
            else:
                destination = formatted_phone

            masked_destination = _mask_contact(channel, destination) if destination else 'your contact'

            return Response(
                {
                    'message': f'OTP sent. Check your {channel.lower()}.',
                    'channel': channel,
                    'destination': masked_destination,
                },
                status=200
            )

        except OTPDeliveryError as e:
            return Response({'detail': str(e)}, status=502)
        except Exception as e:
            import logging
            logger = logging.getLogger('saccosphere.otp')
            logger.exception(f'OTPSendView error: {str(e)}')
            return Response({'detail': 'Internal server error'}, status=500)


class OTPVerifyView(APIView):
    """Verify OTP code."""
    permission_classes = [AllowAny]
    throttle_classes = [OTPVerifyThrottle]

    @swagger_auto_schema(
        operation_summary='Verify OTP code',
        request_body=account_serializers.OTPVerifySerializer,
        responses={200: {'type': 'object', 'properties': {'message': {'type': 'string'}}}},
    )
    def post(self, request):
        serializer = account_serializers.OTPVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        phone_number = serializer.validated_data['phone_number']
        formatted_phone = format_phone_number(phone_number)
        code = serializer.validated_data['code']
        
        try:
            token = verify_otp(formatted_phone, code, 'PHONE_VERIFY')

            # If user exists (existing phone verification), update their phone
            if token.user:
                user = token.user
                user.phone_number = phone_number
                user.phone_verified_at = timezone.now()
                user.save(update_fields=['phone_number', 'phone_verified_at'])
                user_serializer = UserProfileSerializer(user)
                return Response(user_serializer.data, status=200)
            else:
                # For registration, just confirm OTP was verified
                return Response(
                    {'message': 'Phone number verified. Proceed with registration.'},
                    status=200
                )
            
        except OTPError as e:
            return Response({'error': str(e)}, status=400)
        except Exception as e:
            return Response({'error': 'Internal server error'}, status=500)


class OTPResendView(APIView):
    """Resend OTP code."""
    permission_classes = [AllowAny]
    throttle_classes = [OTPSendThrottle, OTPSendIPThrottle]

    @swagger_auto_schema(
        operation_summary='Resend OTP code',
        request_body=account_serializers.OTPRequestSerializer,
        responses={200: {'type': 'object', 'properties': {'message': {'type': 'string'}}},
                  429: {'type': 'object', 'properties': {'error': {'type': 'string'}}},
        }
    )
    def post(self, request):
        serializer = account_serializers.OTPRequestSerializer(
            data=request.data,
            context={'request': request}
        )
        serializer.is_valid(raise_exception=True)

        phone_number = serializer.validated_data['phone_number']
        formatted_phone = format_phone_number(phone_number)
        purpose = serializer.validated_data['purpose']
        channel = serializer.validated_data['channel']

        # Check cooldown
        from django.utils import timezone
        from datetime import timedelta
        from accounts.models import OTPToken

        recent_token = OTPToken.objects.filter(
            phone_number=formatted_phone
        ).order_by('-created_at').first()

        if recent_token:
            time_passed = timezone.now() - recent_token.created_at
            cooldown_period = timedelta(seconds=720)  # 12 minutes = 5/hour

            if time_passed < cooldown_period:
                remaining_seconds = int((cooldown_period - time_passed).total_seconds())
                return Response({
                    'error': f'Too many OTP requests. Try again in {remaining_seconds // 60} minutes.',
                    'seconds_remaining': remaining_seconds
                }, status=429)

        # Invalidate old token and create new one
        try:
            user = get_user_by_phone_number(phone_number)
            if user is None:
                return Response({'error': 'User not found'}, status=400)

            # Mark old tokens as used
            OTPToken.objects.filter(
                phone_number=formatted_phone,
                purpose=purpose,
                is_used=False
            ).update(is_used=True)

            # Create new token
            token = create_otp_token(user, formatted_phone, purpose)
            # Save channel
            token.channel = channel
            token.save(update_fields=['channel'])

            # Get backend and send
            backend = get_otp_backend(channel)
            backend.send(token)

            # Determine destination for response
            if channel == 'EMAIL':
                destination = user.email if user else None
            else:
                destination = formatted_phone

            masked_destination = _mask_contact(channel, destination) if destination else 'your contact'

            return Response(
                {
                    'message': f'OTP sent. Check your {channel.lower()}.',
                    'channel': channel,
                    'destination': masked_destination,
                },
                status=200
            )

        except OTPDeliveryError as e:
            return Response({'detail': str(e)}, status=502)
        except Exception as e:
            import logging
            logger = logging.getLogger('saccosphere.otp')
            logger.exception(f'OTPResendView error: {str(e)}')
            return Response({'detail': 'Internal server error'}, status=500)


class PasswordResetRequestView(APIView):
    """Request password reset via OTP."""
    permission_classes = [AllowAny]

    @swagger_auto_schema(
        operation_summary='Request password reset',
        request_body=account_serializers.PasswordResetRequestSerializer,
        responses={200: {'type': 'object', 'properties': {'message': {'type': 'string'}}}},
    )
    def post(self, request):
        serializer = account_serializers.PasswordResetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        phone_number = serializer.validated_data['phone_number']
        logger = logging.getLogger('saccosphere.otp')

        # Always return 200 (don't reveal if phone exists or is verified)
        try:
            user = get_user_by_phone_number(phone_number)
            if user is None:
                logger.info(
                    f'Password reset requested for non-existent phone={phone_number}'
                )
                return Response(
                    {'message': 'Password reset OTP sent. Check your phone.'},
                    status=200
                )

            # Check if phone is verified
            if user.phone_verified_at is None:
                logger.warning(
                    f'Password reset requested for unverified phone={phone_number}, '
                    f'user={user.email}'
                )
                return Response(
                    {'message': 'Password reset OTP sent. Check your phone.'},
                    status=200
                )

            formatted_phone = format_phone_number(phone_number)
            token = create_otp_token(user, formatted_phone, 'PASSWORD_RESET')

            # Send SMS via unified backend
            backend = get_otp_backend('PHONE')
            backend.send(token)

            logger.info(
                f'Password reset OTP sent to verified phone={phone_number}, '
                f'user={user.email}'
            )
            return Response(
                {'message': 'Password reset OTP sent. Check your phone.'},
                status=200
            )

        except OTPDeliveryError:
            # Log error but don't reveal to user
            logger.error(f'Password reset SMS failed for phone={phone_number}')
            return Response(
                {'message': 'Password reset OTP sent. Check your phone.'},
                status=200
            )
        except Exception as e:
            logger.exception(f'Password reset request error: {str(e)}')
            return Response(
                {'message': 'Password reset OTP sent. Check your phone.'},
                status=200
            )


class PasswordResetConfirmView(APIView):
    """Verify OTP and return a short-lived password reset token."""
    permission_classes = [AllowAny]

    @swagger_auto_schema(
        operation_summary='Verify OTP for password reset',
        request_body=account_serializers.OTPVerifySerializer,
        responses={200: {'type': 'object', 'properties': {'reset_token': {'type': 'string'}}}},
    )
    def post(self, request):
        serializer = account_serializers.OTPVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        phone_number = serializer.validated_data['phone_number']
        formatted_phone = format_phone_number(phone_number)
        code = serializer.validated_data['code']
        logger = logging.getLogger('saccosphere.otp')

        try:
            otp_token = verify_otp(formatted_phone, code, 'PASSWORD_RESET')

            # Create a short-lived password reset token
            from datetime import timedelta

            reset_token = PasswordResetToken.objects.create(
                user=otp_token.user,
                otp_token=otp_token,
                expires_at=timezone.now() + timedelta(minutes=15),
            )

            logger.info(
                f'Password reset token generated for user={otp_token.user.email}, '
                f'phone={phone_number}'
            )

            return Response(
                {'reset_token': str(reset_token.id)},
                status=200
            )

        except OTPError as e:
            logger.warning(
                f'Password reset OTP verification failed for phone={phone_number}: {str(e)}'
            )
            return Response({'error': str(e)}, status=400)
        except Exception as e:
            logger.exception(f'Password reset confirm error: {str(e)}')
            return Response({'error': 'Internal server error'}, status=500)


class PasswordResetCompleteView(APIView):
    """Complete password reset using the reset token."""
    permission_classes = [AllowAny]

    @swagger_auto_schema(
        operation_summary='Complete password reset',
        request_body=account_serializers.PasswordResetCompleteSerializer,
        responses={200: {'type': 'object', 'properties': {'message': {'type': 'string'}}}},
    )
    def post(self, request):
        serializer = account_serializers.PasswordResetCompleteSerializer(
            data=request.data
        )
        serializer.is_valid(raise_exception=True)

        reset_token_id = serializer.validated_data['reset_token']
        new_password = serializer.validated_data['new_password']
        logger = logging.getLogger('saccosphere.otp')

        try:
            reset_token = PasswordResetToken.objects.get(id=reset_token_id)

            # Check if token is expired
            if reset_token.is_expired:
                logger.warning(
                    f'Attempted to use expired password reset token for user={reset_token.user.email}'
                )
                return Response(
                    {'error': 'Reset token has expired. Please request a new password reset.'},
                    status=400
                )

            # Check if token is already used
            if reset_token.is_used:
                logger.warning(
                    f'Attempted to reuse password reset token for user={reset_token.user.email}'
                )
                return Response(
                    {'error': 'Reset token has already been used. Please request a new password reset.'},
                    status=400
                )

            # Update user password
            user = reset_token.user
            user.set_password(new_password)
            user.save(update_fields=['password'])

            # Mark reset token as used
            reset_token.is_used = True
            reset_token.save(update_fields=['is_used'])

            logger.info(
                f'Password reset completed for user={user.email}'
            )

            return Response(
                {'message': 'Password reset successful.'},
                status=200
            )

        except PasswordResetToken.DoesNotExist:
            logger.warning(f'Invalid password reset token used: {reset_token_id}')
            return Response(
                {'error': 'Invalid reset token. Please request a new password reset.'},
                status=400
            )
        except Exception as e:
            logger.exception(f'Password reset complete error: {str(e)}')
            return Response({'error': 'Internal server error'}, status=500)


class DataErasureRequestView(CreateAPIView):
    """Endpoint for authenticated users to submit data erasure requests."""
    permission_classes = [IsAuthenticated]
    serializer_class = account_serializers.DataErasureRequestSerializer

    def post(self, request):
        serializer = self.serializer_class(
            data=request.data,
            context={'request': request}
        )
        serializer.is_valid(raise_exception=True)

        erasure_request = serializer.save()

        # Log the request creation
        log_audit(
            user=request.user,
            action='CREATE',
            resource_type='DataErasureRequest',
            resource_id=str(erasure_request.id),
            new_values={'reason': erasure_request.reason},
            request=request,
        )

        # Notify the user
        from notifications.utils import create_notification
        create_notification(
            user=request.user,
            title='Data Erasure Request Submitted',
            message='Your data erasure request has been submitted and is pending review.',
            category='SYSTEM',
        )

        return Response(
            {
                'id': str(erasure_request.id),
                'status': erasure_request.status,
                'message': 'Your erasure request has been submitted for review.',
            },
            status=201
        )


class DataErasureReviewView(APIView):
    """Staff-only endpoint for reviewing erasure requests."""
    permission_classes = [IsAuthenticated]

    def post(self, request, request_id):
        # Verify staff status
        if not request.user.is_staff:
            return Response(
                {'error': 'Staff access required.'},
                status=403
            )

        try:
            erasure_request = DataErasureRequest.objects.get(id=request_id)
        except DataErasureRequest.DoesNotExist:
            return Response(
                {'error': 'Erasure request not found.'},
                status=404
            )

        serializer = account_serializers.DataErasureReviewSerializer(
            data=request.data,
            context={'request': request}
        )
        serializer.is_valid(raise_exception=True)

        action = serializer.validated_data['action']
        reviewer_notes = serializer.validated_data.get('reviewer_notes', '')

        if erasure_request.status != DataErasureRequest.Status.PENDING:
            return Response(
                {'error': 'This request has already been reviewed.'},
                status=400
            )

        if action == 'approve':
            erasure_request.status = DataErasureRequest.Status.APPROVED
            erasure_request.reviewed_at = timezone.now()
            erasure_request.reviewed_by = request.user
            erasure_request.reviewer_notes = reviewer_notes
            erasure_request.save()

            # Log approval
            log_audit(
                user=request.user,
                action='APPROVE',
                resource_type='DataErasureRequest',
                resource_id=str(erasure_request.id),
                old_values={'status': 'PENDING'},
                new_values={'status': 'APPROVED', 'reviewer_notes': reviewer_notes},
                request=request,
            )

            # Perform anonymization
            self._anonymize_user(erasure_request)

            # Update status to completed
            erasure_request.status = DataErasureRequest.Status.COMPLETED
            erasure_request.completed_at = timezone.now()
            erasure_request.user_email_anonymized = f'anonymized_{erasure_request.id}'
            erasure_request.save()

            # Log completion
            log_audit(
                user=request.user,
                action='COMPLETE',
                resource_type='DataErasureRequest',
                resource_id=str(erasure_request.id),
                old_values={'status': 'APPROVED'},
                new_values={'status': 'COMPLETED'},
                request=request,
            )

            # Notify user
            if erasure_request.user:
                from notifications.utils import create_notification
                create_notification(
                    user=erasure_request.user,
                    title='Data Erasure Completed',
                    message='Your data has been anonymized as requested.',
                    category='SYSTEM',
                )

            return Response(
                {'message': 'Erasure request approved and completed.'},
                status=200
            )

        elif action == 'reject':
            erasure_request.status = DataErasureRequest.Status.REJECTED
            erasure_request.reviewed_at = timezone.now()
            erasure_request.reviewed_by = request.user
            erasure_request.reviewer_notes = reviewer_notes
            erasure_request.save()

            # Log rejection
            log_audit(
                user=request.user,
                action='REJECT',
                resource_type='DataErasureRequest',
                resource_id=str(erasure_request.id),
                old_values={'status': 'PENDING'},
                new_values={'status': 'REJECTED', 'reviewer_notes': reviewer_notes},
                request=request,
            )

            # Notify user
            if erasure_request.user:
                from notifications.utils import create_notification
                create_notification(
                    user=erasure_request.user,
                    title='Data Erasure Request Rejected',
                    message=f'Your erasure request was rejected. Reason: {reviewer_notes or "No reason provided."}',
                    category='SYSTEM',
                )

            return Response(
                {'message': 'Erasure request rejected.'},
                status=200
            )

    def _anonymize_user(self, erasure_request):
        """Anonymize user data in place - idempotent and safe to re-run."""
        logger = logging.getLogger('saccosphere.accounts')
        user = erasure_request.user
        if not user:
            return

        # Anonymize PII fields
        user.first_name = 'Anonymized'
        user.last_name = 'User'
        user.email = f'anonymized_{user.id}@deleted.local'
        user.phone_number = None
        user.is_active = False

        # Clear any encrypted fields if they exist
        if hasattr(user, 'encrypted_fields'):
            for field in user.encrypted_fields:
                setattr(user, field, None)

        user.save()

        # Revoke all active sessions/tokens
        from rest_framework_simplejwt.token_blacklist.models import (
            OutstandingToken,
            BlacklistedToken,
        )

        OutstandingToken.objects.filter(user=user).delete()

        # Log anonymization
        logger.info(
            f'User anonymized: user_id={user.id}, erasure_request_id={erasure_request.id}'
        )


