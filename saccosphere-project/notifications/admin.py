from django.contrib import admin

from .models import DeviceToken, Notification


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = (
        'user_email',
        'title',
        'category',
        'is_read',
        'push_sent',
        'created_at',
    )
    list_filter = ('category', 'is_read', 'push_sent')
    search_fields = ('user__email', 'title')

    @admin.display(description='User email')
    def user_email(self, obj):
        """Return the notification owner's email."""
        return obj.user.email


@admin.register(DeviceToken)
class DeviceTokenAdmin(admin.ModelAdmin):
    list_display = (
        'user_email',
        'platform',
        'is_active',
        'registered_at',
    )
    list_filter = ('platform', 'is_active')
    search_fields = ('user__email',)

    @admin.display(description='User email')
    def user_email(self, obj):
        """Return the device token owner's email."""
        return obj.user.email
