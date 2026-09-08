from django.apps import apps
from django.db import transaction
from rest_framework.generics import (
    CreateAPIView,
    ListAPIView,
    ListCreateAPIView,
    RetrieveAPIView,
    RetrieveUpdateDestroyAPIView,
)
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.views import APIView

from accounts.permissions import IsSaccoAdmin
from config.response import StandardResponseMixin
from saccomanagement.mixins import SaccoScopedMixin

from .models import Membership, SaccoFieldDefinition
from .serializers import (
    MembershipApplySerializer,
    MembershipDetailSerializer,
    MembershipListSerializer,
    SaccoFieldDefinitionAdminSerializer,
    SaccoFieldDefinitionSerializer,
)


class MembershipListView(StandardResponseMixin, ListAPIView):
    serializer_class = MembershipListSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = Membership.objects.select_related(
            'user',
            'sacco',
        ).filter(user=self.request.user)

        sacco = self.request.query_params.get('sacco')
        status = self.request.query_params.get('status')

        if sacco:
            queryset = queryset.filter(sacco_id=sacco)
        if status:
            queryset = queryset.filter(status=status.upper())

        return queryset.order_by('-application_date')

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)

        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = self.get_serializer(queryset, many=True)
        return self.ok(serializer.data)

    @transaction.atomic
    def post(self, request, *args, **kwargs):
        serializer = MembershipApplySerializer(
            data=request.data,
            context=self.get_serializer_context(),
        )
        serializer.is_valid(raise_exception=True)
        membership = serializer.save()
        data = MembershipDetailSerializer(
            membership,
            context=self.get_serializer_context(),
        ).data
        return self.created(data, 'Membership application submitted')


class MembershipApplyView(StandardResponseMixin, CreateAPIView):
    serializer_class = MembershipApplySerializer
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        membership = serializer.save()
        data = MembershipDetailSerializer(
            membership,
            context=self.get_serializer_context(),
        ).data
        return self.created(data, 'Membership application submitted')


class MembershipDetailView(StandardResponseMixin, RetrieveAPIView):
    serializer_class = MembershipDetailSerializer
    permission_classes = [IsAuthenticated]
    lookup_field = 'id'

    def get_queryset(self):
        return Membership.objects.select_related(
            'user',
            'sacco',
        ).filter(user=self.request.user)

    def retrieve(self, request, *args, **kwargs):
        serializer = self.get_serializer(self.get_object())
        return self.ok(serializer.data)


class MembershipLeaveView(StandardResponseMixin, APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, id):
        membership = Membership.objects.filter(
            id=id,
            user=request.user,
        ).first()

        if membership is None:
            return self.not_found('Membership not found')

        if self._has_active_loans(membership):
            return self.bad_request(
                'You cannot leave a SACCO while you have active loans.',
                {'loans': 'Active loans must be cleared first.'},
            )

        membership.status = Membership.Status.LEFT
        membership.save(update_fields=['status', 'updated_at'])
        data = MembershipDetailSerializer(membership).data
        return self.ok(data, 'Membership left successfully')

    def _has_active_loans(self, membership):
        try:
            loan_model = apps.get_model('services', 'Loan')
        except LookupError:
            return False

        return loan_model.objects.filter(
            membership=membership,
            status__in=['ACTIVE', 'APPROVED', 'DISBURSED'],
        ).exists()


class SaccoFieldsView(StandardResponseMixin, ListAPIView):
    serializer_class = SaccoFieldDefinitionSerializer
    permission_classes = [AllowAny]
    pagination_class = None

    def get_queryset(self):
        return SaccoFieldDefinition.objects.filter(
            sacco_id=self.kwargs['sacco_id'],
        ).order_by('display_order')

    def list(self, request, *args, **kwargs):
        serializer = self.get_serializer(self.get_queryset(), many=True)
        return self.ok(serializer.data)


class SaccoFieldDefinitionAdminListCreateView(
    SaccoScopedMixin, StandardResponseMixin, ListCreateAPIView,
):
    """
    List or create custom field definitions for the current SACCO_ADMIN's
    own SACCO.

    GET/POST /api/v1/members/admin/field-definitions/
    """

    serializer_class = SaccoFieldDefinitionAdminSerializer
    permission_classes = [IsAuthenticated, IsSaccoAdmin]
    pagination_class = None

    def get_queryset(self):
        return SaccoFieldDefinition.objects.filter(
            sacco=self.get_sacco_context(),
        ).order_by('display_order')

    def perform_create(self, serializer):
        serializer.save(sacco=self.get_sacco_context())

    def list(self, request, *args, **kwargs):
        serializer = self.get_serializer(self.get_queryset(), many=True)
        return self.ok(serializer.data)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        return self.created(serializer.data)


class SaccoFieldDefinitionAdminDetailView(
    SaccoScopedMixin, StandardResponseMixin, RetrieveUpdateDestroyAPIView,
):
    """
    Retrieve, update, or delete one custom field definition belonging to
    the current SACCO_ADMIN's own SACCO.

    GET/PATCH/DELETE /api/v1/members/admin/field-definitions/<id>/
    """

    serializer_class = SaccoFieldDefinitionAdminSerializer
    permission_classes = [IsAuthenticated, IsSaccoAdmin]
    lookup_field = 'id'

    def get_queryset(self):
        return SaccoFieldDefinition.objects.filter(
            sacco=self.get_sacco_context(),
        )

    def retrieve(self, request, *args, **kwargs):
        serializer = self.get_serializer(self.get_object())
        return self.ok(serializer.data)

    def update(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(
            instance, data=request.data, partial=kwargs.pop('partial', False),
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return self.ok(serializer.data)

    def partial_update(self, request, *args, **kwargs):
        kwargs['partial'] = True
        return self.update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        instance.delete()
        return self.ok(None, 'Field definition deleted')
