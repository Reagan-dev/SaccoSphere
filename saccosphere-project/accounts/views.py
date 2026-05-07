from django.contrib.auth import authenticate
from django.core.exceptions import FieldError
from django.db.models import Count, Q
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework import status
from rest_framework.generics import (
    CreateAPIView,
    ListAPIView,
    RetrieveAPIView,
    RetrieveUpdateAPIView,
)
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from config.response import StandardResponseMixin

from .models import Sacco
from .serializers import (
    PasswordChangeSerializer,
    SaccoDetailSerializer,
    SaccoListSerializer,
    UserLoginSerializer,
    UserProfileSerializer,
    UserRegistrationSerializer,
)


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
