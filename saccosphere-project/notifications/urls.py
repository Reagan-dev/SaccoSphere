from django.urls import path

from .views import (
    DeviceTokenRegisterView,
    MarkAllReadView,
    MarkReadView,
    NotificationListView,
)


app_name = 'notifications'

urlpatterns = [
    path('', NotificationListView.as_view(), name='notification-list'),
    path(
        '<uuid:id>/read/',
        MarkReadView.as_view(),
        name='notification-read',
    ),
    path(
        'read-all/',
        MarkAllReadView.as_view(),
        name='notification-read-all',
    ),
    path(
        'device/',
        DeviceTokenRegisterView.as_view(),
        name='device-token-register',
    ),
]
