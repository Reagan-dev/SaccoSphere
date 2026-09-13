from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.generics import CreateAPIView, ListAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from config.pagination import NotificationPagination
from saccomanagement.audit_logger import log_audit

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

        previous_owner_id = DeviceToken.objects.filter(
            token=token,
        ).exclude(
            user=request.user,
        ).values_list('user_id', flat=True).first()

        device_token, created = DeviceToken.objects.update_or_create(
            token=token,
            defaults={
                'user': request.user,
                'platform': platform,
                'is_active': True,
            },
        )

        if previous_owner_id is not None:
            # A device's FCM/APNs token can legitimately move to another
            # account (e.g. a member logs out and someone else logs in on
            # the same phone), so this is allowed - but it silently moves
            # future push notifications for that physical device from one
            # account to another, so it's worth an audit trail rather than
            # being invisible.
            log_audit(
                request.user,
                'DEVICE_TOKEN_REASSIGNED',
                'DeviceToken',
                device_token.id,
                old_values={'user_id': str(previous_owner_id)},
                new_values={'user_id': str(request.user.id)},
                request=request,
            )

        response_serializer = self.get_serializer(device_token)
        response_status = (
            status.HTTP_201_CREATED if created else status.HTTP_200_OK
        )
        return Response(
            response_serializer.data,
            status=response_status,
        )
