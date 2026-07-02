from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.generics import CreateAPIView, ListAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from config.pagination import NotificationPagination

from .models import DeviceToken, Notification
from .serializers import DeviceTokenSerializer, NotificationSerializer


class NotificationListView(ListAPIView):
    """List notifications for the authenticated user."""

    serializer_class = NotificationSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = NotificationPagination

    def get_queryset(self):
        """Return filtered notifications for the current user."""
        queryset = Notification.objects.filter(
            user=self.request.user,
        ).select_related('user')
        category = self.request.query_params.get('category')
        is_read = self.request.query_params.get('is_read')

        if category:
            queryset = queryset.filter(category=category)

        if is_read is not None:
            if is_read.lower() == 'true':
                queryset = queryset.filter(is_read=True)
            elif is_read.lower() == 'false':
                queryset = queryset.filter(is_read=False)

        return queryset


class MarkReadView(APIView):
    """Mark one notification as read."""

    permission_classes = [IsAuthenticated]

    def post(self, request, id):
        """Set a notification's is_read flag to true."""
        notification = get_object_or_404(
            Notification,
            id=id,
            user=request.user,
        )
        notification.is_read = True
        notification.save(update_fields=['is_read'])
        return Response({'success': True}, status=status.HTTP_200_OK)


class MarkAllReadView(APIView):
    """Mark all notifications for the user as read."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        """Set all unread notifications for the user to read."""
        updated_count = Notification.objects.filter(
            user=request.user,
            is_read=False,
        ).update(is_read=True)

        return Response(
            {
                'success': True,
                'count': updated_count,
            },
            status=status.HTTP_200_OK,
        )


class DeviceTokenRegisterView(CreateAPIView):
    """Register or reactivate a device token for push notifications."""

    serializer_class = DeviceTokenSerializer
    permission_classes = [IsAuthenticated]

    def create(self, request, *args, **kwargs):
        """Create a new token or update an existing one."""
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        token = serializer.validated_data['token']
        platform = serializer.validated_data['platform']
        device_token, created = DeviceToken.objects.update_or_create(
            token=token,
            defaults={
                'user': request.user,
                'platform': platform,
                'is_active': True,
            },
        )
        response_serializer = self.get_serializer(device_token)
        response_status = (
            status.HTTP_201_CREATED if created else status.HTTP_200_OK
        )
        return Response(
            response_serializer.data,
            status=response_status,
        )


