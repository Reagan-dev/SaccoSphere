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


# ============================================================
# REVIEW - READ THIS THEN DELETE FROM THIS LINE TO THE END
# ============================================================
#
# notifications/admin.py
#
# NotificationAdmin registers Notification in Django admin. It shows the user
# email, title, category, read state, push state, and creation date. Filters
# and search make it easier for staff to find user notifications.
#
# DeviceTokenAdmin registers DeviceToken in Django admin. It shows which user
# owns the token, the device platform, whether the token is active, and when it
# was last registered.
#
# notifications/serializers.py
#
# NotificationSerializer controls the notification fields returned by the API.
# id and created_at are read-only because the server creates them.
#
# DeviceTokenSerializer validates device registration input. It accepts a token
# and platform, then the view attaches the authenticated user.
#
# notifications/views.py
#
# NotificationListView returns only the logged-in user's notifications. It can
# filter by category and read/unread state, and uses NotificationPagination.
#
# MarkReadView marks one notification as read, but only if it belongs to the
# logged-in user.
#
# MarkAllReadView marks all unread notifications for the logged-in user as read
# and returns how many rows were updated.
#
# DeviceTokenRegisterView creates or updates a device token. If the token
# already exists, it updates the owner/platform and marks it active again.
#
# notifications/utils.py
#
# create_notification creates a Notification row for in-app delivery. It catches
# and logs errors so a failed notification does not break the loan, KYC,
# payment, or other business action that called it.
#
# Django/Python concepts you might not know well
#
# ListAPIView is a DRF generic view for returning lists of objects. It handles
# serializer use and pagination automatically.
#
# APIView is lower-level. It is useful for custom actions like marking one or
# all notifications as read.
#
# update_or_create finds a row by lookup fields and updates it, or creates it
# when no row exists. It is useful for device tokens because the same device may
# register more than once.
#
# select_related('user') fetches the related user in the same database query,
# avoiding extra queries when admin or serializers need user fields.
#
# One manual test
#
# Log in, create a notification for your user in Django shell, then call
# GET /api/v1/notifications/. Confirm it appears. Then call
# POST /api/v1/notifications/<id>/read/ and confirm is_read changes to true.
#
# Important design decision
#
# create_notification never raises errors to calling code. Notifications are
# useful but secondary; a failed notification should not roll back a successful
# loan, KYC review, or payment workflow.
#
# END OF REVIEW - DELETE EVERYTHING FROM THE FIRST # LINE ABOVE
