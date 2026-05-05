from django.db import IntegrityError
from rest_framework import serializers

from accounts.models import Sacco

from .models import (
    MemberFieldData,
    Membership,
    SaccoApplication,
    SaccoFieldDefinition,
)


class MembershipUserSerializer(serializers.Serializer):
    email = serializers.EmailField()
    full_name = serializers.SerializerMethodField()

    def get_full_name(self, obj):
        return obj.get_full_name()


class MembershipSaccoSerializer(serializers.Serializer):
    name = serializers.CharField()
    logo = serializers.ImageField()


class MembershipListSerializer(serializers.ModelSerializer):
    user = MembershipUserSerializer(read_only=True)
    sacco = MembershipSaccoSerializer(read_only=True)

    class Meta:
        model = Membership
        fields = (
            'id',
            'user',
            'sacco',
            'member_number',
            'status',
            'application_date',
        )


class MembershipDetailSerializer(MembershipListSerializer):
    class Meta(MembershipListSerializer.Meta):
        fields = MembershipListSerializer.Meta.fields + (
            'approved_date',
            'rejection_reason',
            'notes',
        )


class CustomFieldInputSerializer(serializers.Serializer):
    field_id = serializers.UUIDField()
    value = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )


class MembershipApplySerializer(serializers.Serializer):
    sacco = serializers.PrimaryKeyRelatedField(queryset=Sacco.objects.all())
    custom_fields = CustomFieldInputSerializer(many=True, required=False)
    employment_status = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    employer_name = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    monthly_income = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        required=False,
        allow_null=True,
    )

    def validate_sacco(self, sacco):
        if sacco.membership_type == Sacco.MembershipType.CLOSED:
            raise serializers.ValidationError(
                'This SACCO is not accepting public applications.'
            )
        if not sacco.is_active:
            raise serializers.ValidationError('This SACCO is not active.')
        return sacco

    def validate(self, attrs):
        request = self.context.get('request')
        user = getattr(request, 'user', None)
        sacco = attrs['sacco']

        if Membership.objects.filter(user=user, sacco=sacco).exists():
            raise serializers.ValidationError(
                {'sacco': 'You have already applied to this SACCO.'}
            )

        submitted_field_ids = {
            item['field_id'] for item in attrs.get('custom_fields', [])
        }
        valid_fields = SaccoFieldDefinition.objects.filter(
            sacco=sacco,
            id__in=submitted_field_ids,
        )
        valid_field_ids = {field.id for field in valid_fields}
        invalid_field_ids = submitted_field_ids - valid_field_ids

        if invalid_field_ids:
            raise serializers.ValidationError(
                {'custom_fields': 'One or more fields are invalid.'}
            )

        required_field_ids = set(
            SaccoFieldDefinition.objects.filter(
                sacco=sacco,
                is_required=True,
            ).values_list('id', flat=True)
        )
        missing_fields = required_field_ids - submitted_field_ids

        if missing_fields:
            raise serializers.ValidationError(
                {'custom_fields': 'Please complete all required fields.'}
            )

        return attrs

    def create(self, validated_data):
        request = self.context['request']
        custom_fields = validated_data.pop('custom_fields', [])
        application_fields = {
            'employment_status': validated_data.pop(
                'employment_status',
                None,
            ),
            'employer_name': validated_data.pop('employer_name', None),
            'monthly_income': validated_data.pop('monthly_income', None),
        }
        sacco = validated_data['sacco']

        try:
            membership = Membership.objects.create(
                user=request.user,
                sacco=sacco,
                status=Membership.Status.PENDING,
            )
        except IntegrityError as exc:
            raise serializers.ValidationError(
                {'sacco': 'You have already applied to this SACCO.'}
            ) from exc

        SaccoApplication.objects.create(
            user=request.user,
            sacco=sacco,
            status=SaccoApplication.Status.SUBMITTED,
            **application_fields,
        )

        field_map = {
            field.id: field
            for field in SaccoFieldDefinition.objects.filter(sacco=sacco)
        }
        field_data = [
            MemberFieldData(
                membership=membership,
                field=field_map[item['field_id']],
                value=item.get('value'),
            )
            for item in custom_fields
        ]
        MemberFieldData.objects.bulk_create(field_data)

        return membership


class SaccoFieldDefinitionSerializer(serializers.ModelSerializer):
    class Meta:
        model = SaccoFieldDefinition
        fields = (
            'id',
            'label',
            'field_type',
            'is_required',
            'options',
            'display_order',
        )


class FieldSummarySerializer(serializers.Serializer):
    label = serializers.CharField()
    field_type = serializers.CharField()


class MemberFieldDataSerializer(serializers.ModelSerializer):
    field = FieldSummarySerializer(read_only=True)

    class Meta:
        model = MemberFieldData
        fields = (
            'field',
            'value',
            'file_value',
        )


class SaccoApplicationSerializer(serializers.ModelSerializer):
    class Meta:
        model = SaccoApplication
        fields = '__all__'
        read_only_fields = (
            'id',
            'user',
            'status',
            'reviewed_by',
            'review_notes',
            'submitted_at',
            'reviewed_at',
            'created_at',
            'updated_at',
        )
