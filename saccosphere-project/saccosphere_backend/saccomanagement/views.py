from rest_framework import viewsets, status, permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from .models import Management
from .serializers import ManagementSerializer, ManagementDetailSerializer


class ManagementViewSet(viewsets.ModelViewSet):
    queryset = Management.objects.all()

    def get_serializer_class(self):
        if self.action in ["list", "retrieve"]:
            return ManagementDetailSerializer
        return ManagementSerializer

    def get_queryset(self):
        user = self.request.user
        if user.is_staff:
            return Management.objects.all()
        # Regular users only see management of Saccos where they are approved members
        return Management.objects.filter(
            sacco__saccomembership__user=user,
            sacco__saccomembership__status="approved"
        ).distinct()

    def get_permissions(self):
        """
        - Only admins can create, update, delete, or change status.
        - Regular members can only list and retrieve.
        """
        if self.action in ["create", "update", "partial_update", "destroy", "set_status"]:
            permission_classes = [permissions.IsAdminUser]
        else:
            permission_classes = [permissions.IsAuthenticated]
        return [p() for p in permission_classes]

    @action(detail=True, methods=["post"], permission_classes=[permissions.IsAdminUser])
    def set_status(self, request, pk=None):
        """Allow only admins to update management status"""
        mgmt = self.get_object()
        new_status = request.data.get("management")

        valid_choices = [choice[0] for choice in Management.MANAGEMENT_CHOICES]
        if new_status not in valid_choices:
            return Response(
                {"detail": "Invalid status"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        mgmt.management = new_status
        mgmt.save()
        return Response({"management": mgmt.management}, status=status.HTTP_200_OK)
