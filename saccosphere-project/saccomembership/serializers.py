from decimal import Decimal, InvalidOperation

from django.db import IntegrityError
from django.utils.dateparse import parse_date
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
    user_id = serializers.UUIDField(read_only=True)
    sacco = MembershipSaccoSerializer(read_only=True)

    class Meta:
        model = Membership
        fields = (
            'id',
            'user_id',
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

    # TODO(product): confirm a reasonable max length for free-text custom
    # field answers - this is a placeholder, not a product decision.
    TEXT_MAX_LENGTH = 1000
    BOOLEAN_TRUE_VALUES = {'true', '1', 'yes'}
    BOOLEAN_FALSE_VALUES = {'false', '0', 'no'}

    def validate(self, attrs):
        """
        Type-check `value` against the referenced field definition's
        FieldType. The field_id/sacco cross-check (does this field belong
        to the sacco being applied to) happens afterwards in
        MembershipApplySerializer.validate() - if field_id doesn't resolve
        to a real SaccoFieldDefinition at all, skip type-checking here and
        let that later check produce the "invalid field" error instead of
        this method raising a confusing one of its own.
        """
        value = attrs.get('value')
        if not value:
            return attrs

        try:
            field = SaccoFieldDefinition.objects.get(id=attrs['field_id'])
        except SaccoFieldDefinition.DoesNotExist:
            return attrs

        field_type = field.field_type
        if field_type == SaccoFieldDefinition.FieldType.NUMBER:
            try:
                Decimal(value)
            except InvalidOperation:
                raise serializers.ValidationError(
                    {'value': f'"{field.label}" must be a number.'},
                )
        elif field_type == SaccoFieldDefinition.FieldType.DATE:
            if parse_date(value) is None:
                raise serializers.ValidationError(
                    {
                        'value': (
                            f'"{field.label}" must be a valid date in '
                            'YYYY-MM-DD format.'
                        ),
                    },
                )
        elif field_type == SaccoFieldDefinition.FieldType.BOOLEAN:
            normalized = value.strip().lower()
            allowed = self.BOOLEAN_TRUE_VALUES | self.BOOLEAN_FALSE_VALUES
            if normalized not in allowed:
                raise serializers.ValidationError(
                    {
                        'value': (
                            f'"{field.label}" must be one of: '
                            'true/false, 1/0, yes/no.'
                        ),
                    },
                )
        elif field_type == SaccoFieldDefinition.FieldType.SELECT:
            options = field.options or []
            if value not in options:
                raise serializers.ValidationError(
                    {
                        'value': (
                            f'"{field.label}" must be one of: '
                            f'{", ".join(str(o) for o in options)}.'
                        ),
                    },
                )
        elif field_type == SaccoFieldDefinition.FieldType.FILE:
            # No file-upload path exists for custom fields anywhere in this
            # codebase today (confirmed by search) - CustomFieldInputSerializer
            # only ever carries a text `value`, so there is no way to submit
            # a genuine file reference through this endpoint. Reject rather
            # than silently store arbitrary text as if it were a file
            # reference - that is a product gap (a real upload flow for
            # custom FILE fields), not something to invent here.
            raise serializers.ValidationError(
                {
                    'value': (
                        f'"{field.label}" is a file field. File uploads '
                        'for custom fields are not supported by this '
                        'endpoint.'
                    ),
                },
            )
        elif field_type == SaccoFieldDefinition.FieldType.TEXT:
            if len(value) > self.TEXT_MAX_LENGTH:
                raise serializers.ValidationError(
                    {
                        'value': (
                            f'"{field.label}" must be at most '
                            f'{self.TEXT_MAX_LENGTH} characters.'
                        ),
                    },
                )

        return attrs


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
        # STAFF_ONLY is treated the same as CLOSED here: this codebase has
        # no staff-verification mechanism (no Employee/StaffMember model,
        # invitation code, or allow-listed email domain - confirmed by
        # repo-wide search) to distinguish a qualifying staff applicant
        # from any other public applicant. Building member creation for
        # STAFF_ONLY SACCOs is a product gap for a supported path (e.g.
        # admin-driven import) to solve, not something to invent here.
        if sacco.membership_type in (
            Sacco.MembershipType.CLOSED,
            Sacco.MembershipType.STAFF_ONLY,
        ):
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


class SaccoFieldDefinitionAdminSerializer(serializers.ModelSerializer):
    """
    Write-enabled counterpart to SaccoFieldDefinitionSerializer, used only
    by the SACCO_ADMIN-scoped CRUD view. `sacco` is deliberately excluded -
    the view sets it from the admin's own SACCO context, not client input.
    """

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
        read_only_fields = ('id',)

    def validate_options(self, value):
        field_type = self.initial_data.get(
            'field_type', getattr(self.instance, 'field_type', None),
        )
        if field_type == SaccoFieldDefinition.FieldType.SELECT and (
            not value
            or not isinstance(value, list)
            or not all(isinstance(item, str) for item in value)
        ):
            raise serializers.ValidationError(
                'A SELECT field must declare a non-empty list of string '
                'options.',
            )
        return value

    def validate(self, attrs):
        changing_field_type = (
            self.instance is not None
            and 'field_type' in attrs
            and attrs['field_type'] != self.instance.field_type
        )
        if changing_field_type and MemberFieldData.objects.filter(
            field=self.instance,
        ).exists():
            raise serializers.ValidationError(
                {
                    'field_type': (
                        'This field already has submitted member data - '
                        'its type cannot be changed. Create a new field '
                        'instead.'
                    ),
                },
            )
        return attrs


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
