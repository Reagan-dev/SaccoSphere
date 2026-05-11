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

from .models import KYCVerification, Sacco, User
from . import serializers as account_serializers
from .permissions import IsSaccoAdminOrSuperAdmin
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
from .otp_utils import create_otp_token, verify_otp, OTPError
from .integrations.otp_service import ATSMSClient, ATSMSError
from .throttles import OTPSendThrottle


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
        data = {
            'access': str(refresh.access_token),
            'refresh': str(refresh),
            'user': UserProfileSerializer(user).data,
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

    def post(self, request):
        """Validate and save a KYC document upload."""
        serializer = KYCUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        document_type = serializer.validated_data['document_type']
        uploaded_file = serializer.validated_data['file']
        kyc, _ = KYCVerification.objects.get_or_create(
            user=request.user,
            defaults={'status': KYCVerification.Status.NOT_STARTED},
        )

        setattr(kyc, document_type, uploaded_file)
        update_fields = [document_type]

        if kyc.id_front and kyc.id_back:
            kyc.status = KYCVerification.Status.PENDING
            kyc.submitted_at = timezone.now()
            kyc.rejection_reason = ''
            update_fields.extend([
                'status',
                'submitted_at',
                'rejection_reason',
            ])

        kyc.save(update_fields=update_fields)
        response_serializer = KYCStatusSerializer(
            kyc,
            context={'request': request},
        )
        return Response(
            response_serializer.data,
            status=status.HTTP_200_OK,
        )


class KYCStatusView(RetrieveAPIView):
    """Return the authenticated user's KYC status."""

    serializer_class = KYCStatusSerializer
    permission_classes = [IsAuthenticated]

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
        queryset = KYCVerification.objects.select_related('user')
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

        kyc.status = review_status
        kyc.rejection_reason = rejection_reason
        kyc.reviewed_by = request.user

        update_fields = ['status', 'rejection_reason', 'reviewed_by']
        if review_status == KYCVerification.Status.APPROVED:
            kyc.verified_at = timezone.now()
            update_fields.append('verified_at')

        kyc.save(update_fields=update_fields)
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


class AdminKYCQueueView(AdminKYCQuerysetMixin, ListAPIView):
    """List pending KYC verification records for admin review."""

    serializer_class = KYCStatusSerializer
    permission_classes = [IsSaccoAdminOrSuperAdmin]

    def get_queryset(self):
        """Return pending KYC records visible to the current admin."""
        return super().get_queryset().filter(
            status=KYCVerification.Status.PENDING,
        )


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
    """Send OTP to user's phone number."""
    permission_classes = [AllowAny]
    throttle_classes = [OTPSendThrottle]

    @swagger_auto_schema(
        operation_summary='Send OTP code',
        request_body=account_serializers.OTPRequestSerializer,
        responses={200: {'type': 'object', 'properties': {'message': {'type': 'string'}}}},
    )
    def post(self, request):
        serializer = account_serializers.OTPRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        phone_number = serializer.validated_data['phone_number']
        purpose = serializer.validated_data['purpose']
        
        # Find user for phone verification or password reset
        user = None
        if purpose in ['PHONE_VERIFY', 'PASSWORD_RESET']:
            try:
                user = User.objects.get(phone_number=phone_number)
            except User.DoesNotExist:
                # For password reset, don't reveal if phone exists
                if purpose == 'PASSWORD_RESET':
                    return Response({'message': 'OTP sent. Check your phone.'}, status=200)
                else:
                    return Response({'error': 'Phone number not found'}, status=400)
        
        if not user:
            return Response({'error': 'User not found'}, status=400)
        
        try:
            # Create OTP token
            token = create_otp_token(user, phone_number, purpose)
            
            # Send SMS
            client = ATSMSClient()
            result = client.send_otp(phone_number, token.code, purpose)
            
            if result:
                return Response({'message': 'OTP sent. Check your phone.'}, status=200)
            else:
                return Response({'error': 'Failed to send OTP'}, status=502)
                
        except ATSMSError as e:
            return Response({'error': str(e)}, status=502)
        except Exception as e:
            return Response({'error': 'Internal server error'}, status=500)


class OTPVerifyView(APIView):
    """Verify OTP code."""
    permission_classes = [AllowAny]

    @swagger_auto_schema(
        operation_summary='Verify OTP code',
        request_body=account_serializers.OTPVerifySerializer,
        responses={200: UserProfileSerializer},
    )
    def post(self, request):
        serializer = account_serializers.OTPVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        phone_number = serializer.validated_data['phone_number']
        code = serializer.validated_data['code']
        
        try:
            token = verify_otp(phone_number, code, 'PHONE_VERIFY')
            
            # Update user's phone number
            user = token.user
            user.phone_number = phone_number
            user.save(update_fields=['phone_number'])
            
            # Return user data
            user_serializer = UserProfileSerializer(user)
            return Response(user_serializer.data, status=200)
            
        except OTPError as e:
            return Response({'error': str(e)}, status=400)
        except Exception as e:
            return Response({'error': 'Internal server error'}, status=500)


class OTPResendView(APIView):
    """Resend OTP code."""
    permission_classes = [AllowAny]
    throttle_classes = [OTPSendThrottle]

    @swagger_auto_schema(
        operation_summary='Resend OTP code',
        request_body=account_serializers.OTPRequestSerializer,
        responses={200: {'type': 'object', 'properties': {'message': {'type': 'string'}}},
                  429: {'type': 'object', 'properties': {'error': {'type': 'string'}}},
        }
    )
    def post(self, request):
        serializer = account_serializers.OTPRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        phone_number = serializer.validated_data['phone_number']
        purpose = serializer.validated_data['purpose']
        
        # Check cooldown
        from django.utils import timezone
        from datetime import timedelta
        from accounts.models import OTPToken
        
        recent_token = OTPToken.objects.filter(
            phone_number=phone_number
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
            user = User.objects.get(phone_number=phone_number)
            
            # Mark old tokens as used
            OTPToken.objects.filter(
                phone_number=phone_number,
                purpose=purpose,
                is_used=False
            ).update(is_used=True)
            
            # Create new token
            token = create_otp_token(user, phone_number, purpose)
            
            # Send SMS
            client = ATSMSClient()
            result = client.send_otp(phone_number, token.code, purpose)
            
            if result:
                return Response({'message': 'OTP sent. Check your phone.'}, status=200)
            else:
                return Response({'error': 'Failed to send OTP'}, status=502)
                
        except User.DoesNotExist:
            return Response({'error': 'User not found'}, status=400)
        except ATSMSError as e:
            return Response({'error': str(e)}, status=502)
        except Exception as e:
            return Response({'error': 'Internal server error'}, status=500)


class PasswordResetRequestView(APIView):
    """Request password reset via OTP."""
    permission_classes = [AllowAny]

    @swagger_auto_schema(
        operation_summary='Request password reset',
        request_body=account_serializers.OTPRequestSerializer,
        responses={200: {'type': 'object', 'properties': {'message': {'type': 'string'}}}},
    )
    def post(self, request):
        serializer = account_serializers.OTPRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        phone_number = serializer.validated_data['phone_number']
        
        # Always return 200 (don't reveal if phone exists)
        try:
            user = User.objects.get(phone_number=phone_number)
            token = create_otp_token(user, phone_number, 'PASSWORD_RESET')
            
            # Send SMS
            client = ATSMSClient()
            result = client.send_otp(phone_number, token.code, 'PASSWORD_RESET')
            
            if result:
                return Response({'message': 'Password reset OTP sent. Check your phone.'}, status=200)
            else:
                return Response({'message': 'Password reset OTP sent. Check your phone.'}, status=200)
                
        except User.DoesNotExist:
            # Don't reveal if phone exists
            return Response({'message': 'Password reset OTP sent. Check your phone.'}, status=200)
        except Exception as e:
            return Response({'message': 'Password reset OTP sent. Check your phone.'}, status=200)


class PasswordResetConfirmView(APIView):
    """Confirm password reset with OTP."""
    permission_classes = [AllowAny]

    @swagger_auto_schema(
        operation_summary='Confirm password reset',
        request_body=account_serializers.PasswordResetConfirmSerializer,
        responses={200: {'type': 'object', 'properties': {'message': {'type': 'string'}}}},
    )
    def post(self, request):
        serializer = account_serializers.PasswordResetConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        phone_number = serializer.validated_data['phone_number']
        code = serializer.validated_data['code']
        new_password = serializer.validated_data['new_password']
        
        try:
            token = verify_otp(phone_number, code, 'PASSWORD_RESET')
            
            # Update user password
            user = token.user
            user.set_password(new_password)
            user.save(update_fields=['password'])
            
            return Response({'message': 'Password reset successful.'}, status=200)
            
        except OTPError as e:
            return Response({'error': str(e)}, status=400)
        except Exception as e:
            return Response({'error': 'Internal server error'}, status=500)


# ============================================================
# REVIEW - READ THIS THEN DELETE FROM THIS LINE TO THE END
# ============================================================
#
# accounts/storage.py
#
# KYCDocumentStorage is the storage class used by KYC document ImageFields.
# Right now it inherits Django's FileSystemStorage, so files are saved under
# the normal MEDIA_ROOT in development. The TODO marks the production upgrade:
# use django-storages with an S3-compatible private bucket.
#
# accounts/models.py
#
# KYCVerification now uses KYCDocumentStorage for id_front, id_back, passport,
# and huduma document uploads. The huduma field was added because the upload
# API accepts document_type='huduma' and needs a real file field to save into.
#
# accounts/serializers.py
#
# KYCStatusSerializer returns the member's current KYC status and document
# URLs. Members can read these fields but cannot set review status directly.
#
# KYCUploadSerializer validates multipart upload input. It checks document_type,
# file size, extension, and minimum image dimensions before the view saves the
# file. PDFs are allowed but skip image dimension checks.
#
# AdminKYCReviewSerializer validates admin review decisions. It only accepts
# APPROVED or REJECTED, and requires rejection_reason when status is REJECTED.
#
# accounts/views.py
#
# KYCUploadView lets an authenticated user upload one KYC document at a time.
# It creates the user's KYC record if missing, saves the file to the matching
# field, and moves the KYC status to PENDING when id_front and id_back exist.
#
# KYCStatusView returns the authenticated user's KYC record. If the user does
# not have one yet, it creates a NOT_STARTED record and returns it.
#
# AdminKYCQueueView lists PENDING KYC records for review. Super admins see all
# pending records. SACCO admins see pending records only for users who belong
# to SACCOs they administer.
#
# AdminKYCReviewView lets an admin approve or reject a KYC record. It records
# reviewed_by, sets verified_at when approved, saves rejection_reason when
# rejected, and creates a notification for the member.
#
# accounts/permissions.py
#
# IsSaccoAdminOrSuperAdmin now understands user-owned objects such as KYC. A
# SACCO admin can review KYC only for users who belong to one of that admin's
# SACCOs. Super admins can review any KYC record.
#
# Django/Python concepts you might not know well
#
# multipart/form-data is the request format browsers and clients use for file
# uploads. DRF needs MultiPartParser/FormParser so request.data includes files.
#
# ImageField is a Django file field intended for images. In this implementation
# it also stores PDFs because model validation is handled at the serializer
# layer before saving.
#
# select_related('user') fetches each KYC record and its related user in one
# database query. That avoids extra queries when serializing admin queues.
#
# A lazy import means importing inside a method instead of at the top of the
# file. AdminKYCReviewView imports create_notification only when a review is
# completed, which reduces circular import risk.
#
# One manual test
#
# Log in as a normal user and upload a 400x300 or larger PNG to
# POST /api/v1/accounts/kyc/upload/ with document_type=id_front, then upload
# id_back. Call GET /api/v1/accounts/kyc/status/ and confirm status is PENDING
# and both document URLs are present.
#
# Important design decision
#
# Upload validation lives in KYCUploadSerializer, while save/review workflow
# lives in views. That keeps input validation reusable and keeps business state
# changes close to the API actions that cause them.
#
# END OF REVIEW - DELETE EVERYTHING FROM THE FIRST # LINE ABOVE
# ============================================================


