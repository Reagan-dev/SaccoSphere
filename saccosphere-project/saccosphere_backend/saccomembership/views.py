from django.http import JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from rest_framework import viewsets,permissions,status
from rest_framework.decorators import action
from rest_framework.response import Response 
from .models import Membership, MembershipFieldData, Sacco
from .serializers import MembershipSerializer,MembershipDetailSerializer

class MembershipViewSet(viewsets.ModelViewSet):
    queryset = Membership.objects.all()
    permissions_classes = [permissions.IsAuthenticated]
    
    
    def get_serializer_class(self):
        if self.action in ['list','retrieve']:
            return MembershipDetailSerializer
        return MembershipSerializer
    
    def query_set(self):
        user = self.request.user
        if user.is_staff:
            return Membership.objects.all()
        return Membership.objects.filter(user=user)
    
    def perform_create(self,serializer):
        serializer.save(user=self.request.user)
        #admin can approve membership 
    @action(detail=True, methods=['post'], permission_classes=[permissions.IsAdminUser])
    def approve(self, request, pk=None):
        membership = self.get_object()
        membership.status = 'approved'
        membership.is_active = True
        membership.save()
        return Response({'status': 'approved'}, status=status.HTTP_200_OK)
       #admin can reject membership
    @action(detail=True, methods=['post'], permission_classes=[permissions.IsAdminUser])
    def reject(self, request, pk=None):
        membership = self.get_object()
        membership.status = 'rejected'
        membership.is_active = False
        membership.save()
        return Response({'status': 'rejected'}, status=status.HTTP_200_OK)
    #members and admin can mark themselves as left
    @action(detail=True, methods=['post'], permission_classes=[permissions.IsAuthenticated])
    def leave(self, request, pk=None):
        membership = self.get_object()

        if request.user != membership.user and not request.user.is_staff:
            return Response({'detail': 'Not allowed'}, status=status.HTTP_403_FORBIDDEN)
        membership.status = 'left'
        membership.is_active = False
        membership.save()
        return Response({'status': 'left'}, status=status.HTTP_200_OK)
        
    

def join_sacco(request, uuid):
    sacco = get_object_or_404(Sacco, id=uuid)

    if not sacco.is_internal:
        return redirect(sacco.website_url)
    
    if request.method == 'POST':
        membership = Membership.objects.create(
            user=request.user,
            sacco=sacco,
        )
        for field in sacco.custom_fields.all():
            value = request.POST.get(field.field_name)
            MembershipFieldData.objects.create(
                membership=membership,
                sacco_field=field,
                value=value
            )
        return JsonResponse({"message": "Registration submitted for approval"})
    
    custom_fields = sacco.custom_fields.all()
    return render(request, 'join_sacco.html', {'sacco': sacco, 'fields': custom_fields})
