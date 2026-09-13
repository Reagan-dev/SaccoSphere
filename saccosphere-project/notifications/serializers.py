from rest_framework import serializers

from .models import DeviceToken, Notification


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = (
            'id',
            'title',
            'message',
            'category',
            'is_read',
            'action_url',
            'created_at',
        )
        read_only_fields = ('id', 'created_at')


class DeviceTokenSerializer(serializers.ModelSerializer):
    class Meta:
        model = DeviceToken
        fields = (
            'token',
            'platform',
        )
        # DeviceTokenRegisterView.create() upserts on `token` via
        # update_or_create() to reactivate an already-registered token
        # (e.g. the same device re-sending its FCM token on app restart).
        # The default ModelSerializer would add a UniqueValidator on this
        # unique field that rejects that exact case with a 400 before the
        # view ever runs - drop it so re-registration reaches the view.
        extra_kwargs = {
            'token': {'validators': []},
        }
