from rest_framework import serializers

from .models import Role


class RoleSerializer(serializers.ModelSerializer):
    user_email = serializers.CharField(
        source='user.email',
        read_only=True,
    )
    sacco_name = serializers.CharField(
        source='sacco.name',
        read_only=True,
        allow_null=True,
    )
    role_name = serializers.CharField(
        source='get_name_display',
        read_only=True,
    )

    class Meta:
        model = Role
        fields = (
            'id',
            'user_email',
            'sacco_name',
            'role_name',
            'created_at',
        )
        read_only_fields = fields
