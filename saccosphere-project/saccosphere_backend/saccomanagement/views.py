from django.shortcuts import render
from rest_framework import viesets,status,permissions
from rest_framework.response import Response
from rest_framework.decorators import action
from .models import Management
from .serializers import ManagementSerializer, ManagementDetailSerializer
from saccomembership.models import Member


class ManagementViewSet(viesets.ModelViewSet):
    query_set = Management.objects.all()
    permission_classes = [permissions.IsAuthenticated]
    
    def get_serilaizer_class(self):
        if self.action in ['list','retrieve']:
            return ManagementDetailSerializer
        return ManagementSerializer
    
    def get_query_set(self):
        user= self.request .user
        if user.is_staff:
            return Management.objects.all()
        
        return Management.objects.filter(
            sacco__saccomembership__user=user,
            sacco__saccomembership__status='approved'
        ).distinct() 
    def perform_create(self, serializer):
        
        user = self.request.user
        if not user.is_staff:
            raise permissions.PermissionDenied("Only administrators can create management records.")
        serializer.save()

    @action(detail=True, methods=['post'], permission_classes=[permissions.IsAdminUser])
    def set_status(self, request, pk=None):
       
        mgmt = self.get_object()
        new_status = request.data.get('management')
        valid_choices = [choice[0] for choice in Management.MANAGEMENT_CHOICES]
        if new_status not in valid_choices:
            return Response({'detail': 'Invalid status'}, status=status.HTTP_400_BAD_REQUEST)
        mgmt.management = new_status
        mgmt.save()
        return Response({'management': mgmt.management}, status=status.HTTP_200_OK)