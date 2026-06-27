from decimal import Decimal
from pathlib import Path

from django.conf import settings
from rest_framework import serializers

from accounts.serializers import validate_kenyan_phone_number
from services.models import Loan

from .models import ExternalGuarantor


ALLOWED_IMAGE_EXTENSIONS = {'jpg', 'jpeg', 'png'}


def validate_external_guarantor_image(value):
    max_size = settings.FILE_UPLOAD_MAX_MEMORY_SIZE
    extension = Path(value.name).suffix.lower().lstrip('.')

    if value.size >= max_size:
        raise serializers.ValidationError('File size must be less than 5MB.')

    if extension not in ALLOWED_IMAGE_EXTENSIONS:
        raise serializers.ValidationError(
            'File extension must be jpg, jpeg, or png.'
        )

    return value


class ExternalGuarantorCreateSerializer(serializers.ModelSerializer):
    loan_id = serializers.UUIDField(write_only=True)
    phone_number = serializers.CharField(
        validators=[validate_kenyan_phone_number],
    )
    id_number = serializers.CharField(max_length=20)
    monthly_income = serializers.DecimalField(
        max_digits=10,
        decimal_places=2,
        min_value=Decimal('0.00'),
    )
    guarantee_amount = serializers.DecimalField(
        max_digits=10,
        decimal_places=2,
        min_value=Decimal('100.00'),
    )
    id_front = serializers.ImageField(
        required=False,
        allow_null=True,
        validators=[validate_external_guarantor_image],
    )
    id_back = serializers.ImageField(
        required=False,
        allow_null=True,
        validators=[validate_external_guarantor_image],
    )

    class Meta:
        model = ExternalGuarantor
        fields = (
            'loan_id',
            'full_name',
            'phone_number',
            'id_number',
            'employment_status',
            'monthly_income',
            'guarantee_amount',
            'id_front',
            'id_back',
        )

    def validate_id_number(self, value):
        if not value.isdigit() or len(value) != 8:
            raise serializers.ValidationError(
                'ID number must contain exactly 8 digits.'
            )
        return value

    def validate(self, attrs):
        request = self.context['request']
        loan = self._get_owned_loan(attrs['loan_id'], request.user)
        guarantee_amount = attrs['guarantee_amount']

        if guarantee_amount > loan.amount:
            raise serializers.ValidationError(
                {
                    'guarantee_amount': (
                        'Guarantee amount cannot exceed loan amount.'
                    ),
                }
            )

        exists = ExternalGuarantor.objects.filter(
            loan=loan,
            id_number=attrs['id_number'],
            status=ExternalGuarantor.Status.APPROVED_BY_ADMIN,
        ).exists()
        if exists:
            raise serializers.ValidationError(
                {
                    'id_number': (
                        'This external guarantor is already approved for '
                        'this loan.'
                    ),
                }
            )

        attrs['loan'] = loan
        return attrs

    def create(self, validated_data):
        loan = validated_data.pop('loan')
        validated_data.pop('loan_id')
        return ExternalGuarantor.objects.create(
            loan=loan,
            requested_by=self.context['request'].user,
            sacco=loan.membership.sacco,
            **validated_data,
        )

    def _get_owned_loan(self, loan_id, user):
        try:
            return Loan.objects.select_related(
                'membership',
                'membership__sacco',
                'membership__user',
            ).get(id=loan_id, membership__user=user)
        except Loan.DoesNotExist as exc:
            raise serializers.ValidationError(
                {'loan_id': 'Loan not found for this user.'}
            ) from exc


class ExternalGuarantorDetailSerializer(serializers.ModelSerializer):
    requested_by_name = serializers.SerializerMethodField()
    sacco_name = serializers.SerializerMethodField()
    id_front_url = serializers.SerializerMethodField()
    id_back_url = serializers.SerializerMethodField()

    class Meta:
        model = ExternalGuarantor
        exclude = ('response_token',)
        read_only_fields = tuple(
            field.name
            for field in ExternalGuarantor._meta.fields
            if field.name != 'response_token'
        ) + (
            'requested_by_name',
            'sacco_name',
            'id_front_url',
            'id_back_url',
        )

    def get_requested_by_name(self, obj):
        return obj.requested_by.get_full_name() or obj.requested_by.email

    def get_sacco_name(self, obj):
        return obj.sacco.name

    def get_id_front_url(self, obj):
        return self._build_file_url(obj.id_front)

    def get_id_back_url(self, obj):
        return self._build_file_url(obj.id_back)

    def _build_file_url(self, file_field):
        if not file_field:
            return None

        request = self.context.get('request')
        if request is None:
            return file_field.url

        return request.build_absolute_uri(file_field.url)


class ExternalGuarantorResponseSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=('ACCEPT', 'DECLINE'))
    notes = serializers.CharField(
        max_length=300,
        required=False,
        allow_blank=True,
    )
