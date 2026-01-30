from django.http import JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from rest_framework import viewsets,permissions,status
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework import viewsets, permissions, status
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from .models import Membership, SaccoField, MembershipFieldData
from accounts.models import Sacco
from .serializers import (
    MembershipCreateSerializer,
    MembershipDetailSerializer,
    SaccoFieldSerializer
)


class MembershipViewSet(viewsets.ModelViewSet):
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if user.is_staff:
            return Membership.objects.all()
        return Membership.objects.filter(user=user)

    def get_serializer_class(self):
        if self.action in ['list', 'retrieve']:
            return MembershipDetailSerializer
        return MembershipCreateSerializer

    def perform_create(self, serializer):
        serializer.save()

    @action(detail=True, methods=['post'], permission_classes=[permissions.IsAdminUser])
    def approve(self, request, pk=None):
        membership = self.get_object()
        membership.status = 'approved'
        membership.is_active = True
        membership.save()
        return Response({'status': 'approved'})

    @action(detail=True, methods=['post'], permission_classes=[permissions.IsAdminUser])
    def reject(self, request, pk=None):
        membership = self.get_object()
        membership.status = 'rejected'
        membership.is_active = False
        membership.save()
        return Response({'status': 'rejected'})

    @action(detail=True, methods=['post'], permission_classes=[permissions.IsAuthenticated])
    def leave(self, request, pk=None):
        membership = self.get_object()

        if request.user != membership.user and not request.user.is_staff:
            return Response(
                {'detail': 'Not allowed'},
                status=status.HTTP_403_FORBIDDEN
            )

        membership.status = 'left'
        membership.is_active = False
        membership.save()
        return Response({'status': 'left'})
    
class SaccoFieldViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = SaccoFieldSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        # Filter by sacco ID from query parameter `id`
        sacco_id = self.request.query_params.get('id')
        if sacco_id:
            return SaccoField.objects.filter(sacco_id=sacco_id).order_by('order')
        return SaccoField.objects.none()  # return empty if no id provided