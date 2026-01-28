from rest_framework import serializers
from .models import Membership, SaccoField, MembershipFieldData
from accounts.serializers import UserSerializer, SaccoSerializer


class MembershipCreateSerializer(serializers.ModelSerializer):
    fields = serializers.DictField(
        child=serializers.JSONField(),
        write_only=True
    )

    class Meta:
        model = Membership
        fields = [
            'id',
            'sacco',
            'status',
            'date_joined',
            'is_active',
            'fields',
        ]
        read_only_fields = [
            'id',
            'status',
            'date_joined',
            'is_active',
        ]

    def validate(self, data):
        request = self.context.get('request')

        if not request or not request.user.is_authenticated:
            raise serializers.ValidationError("Authentication required.")

        user = request.user
        sacco = data['sacco']
        submitted_fields = data.get('fields', {})

        # Prevent duplicate membership
        if Membership.objects.filter(user=user, sacco=sacco).exists():
            raise serializers.ValidationError(
                {"detail": "You are already a member of this sacco."}
            )

        sacco_fields = SaccoField.objects.filter(sacco=sacco)
        field_map = {f.field_key: f for f in sacco_fields}

        errors = {}

        # Validate required + unknown fields
        for field in sacco_fields:
            value = submitted_fields.get(field.field_key)

            if field.required and not value:
                errors[field.field_key] = "This field is required."

        for key in submitted_fields.keys():
            if key not in field_map:
                errors[key] = "Invalid field."

        if errors:
            raise serializers.ValidationError({"fields": errors})

        return data

    def create(self, validated_data):
        request = self.context.get('request')
        user = request.user
        fields_data = validated_data.pop('fields')
        sacco = validated_data['sacco']

        membership = Membership.objects.create(
            user=user,
            sacco=sacco,
            status='pending'
        )

        sacco_fields = SaccoField.objects.filter(sacco=sacco)
        field_map = {field.field_key: field for field in sacco_fields}

        for key, value in fields_data.items():
            MembershipFieldData.objects.create(
                membership=membership,
                sacco_field=field_map[key],
                value=value
            )

        return membership
    
class MembershipDetailSerializer(serializers.ModelSerializer):
    sacco = SaccoSerializer(read_only=True)
    user = UserSerializer(read_only=True)

    class Meta:
        model = Membership
        fields = ['id', 'user', 'sacco', 'status', 'date_joined', 'is_active']
        read_only_fields = ['id', 'date_joined', 'is_active']
   
