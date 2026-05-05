from django.apps import apps
from django.db import transaction
from rest_framework.generics import CreateAPIView, ListAPIView, RetrieveAPIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.views import APIView

from config.response import StandardResponseMixin

from .models import Membership, SaccoFieldDefinition
from .serializers import (
    MembershipApplySerializer,
    MembershipDetailSerializer,
    MembershipListSerializer,
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
