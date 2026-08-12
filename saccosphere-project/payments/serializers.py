from decimal import Decimal

from django.conf import settings
from rest_framework import serializers

from accounts.models import Sacco
from saccomembership.models import Membership

from .models import Callback, MpesaTransaction, Transaction
from .validators import validate_mpesa_phone


class DepositRequestSerializer(serializers.Serializer):
    """Validate a deposit request and attach the platform fee breakdown."""

    phone_number = serializers.CharField(max_length=15)
    amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    sacco_id = serializers.PrimaryKeyRelatedField(
        source='sacco',
        queryset=Sacco.objects.all(),
    )

    def validate_phone_number(self, value):
        return validate_mpesa_phone(value)

    def validate_amount(self, value):
        if value <= Decimal('0.00'):
            raise serializers.ValidationError(
                'Amount must be greater than zero.'
            )

        if value > Decimal('300000.00'):
            raise serializers.ValidationError(
                'Amount cannot be more than 300000.'
            )

        return value

    def validate(self, data):
        net_amount = data['amount']
        fee_rate = Decimal(str(settings.PLATFORM_FEES['deposit']))
        platform_fee = (net_amount * fee_rate).quantize(Decimal('0.01'))
        gross_amount = net_amount + platform_fee

        data['net_amount'] = net_amount
        data['platform_fee'] = platform_fee
        data['gross_amount'] = gross_amount
        data['fee_rate'] = fee_rate
        return data

    def validate_membership(self, user):
        """Return True when the user may deposit into the target SACCO."""
        sacco = self.validated_data['sacco']
        return Membership.objects.filter(
            user=user,
            sacco=sacco,
            status=Membership.Status.APPROVED,
        ).exists()


class TransactionSerializer(serializers.ModelSerializer):
    provider_name = serializers.CharField(
        source='provider.name',
        read_only=True,
    )

    class Meta:
        model = Transaction
        fields = (
            'id',
            'provider',
            'provider_name',
            'reference',
            'external_reference',
            'transaction_type',
            'amount',
            'fee_amount',
            'currency',
            'status',
            'description',
            'metadata',
            'created_at',
            'updated_at',
        )
        read_only_fields = fields


class MpesaTransactionSerializer(serializers.ModelSerializer):
    class Meta:
        model = MpesaTransaction
        fields = (
            'id',
            'transaction',
            'phone_number',
            'merchant_request_id',
            'checkout_request_id',
            'conversation_id',
            'originator_conversation_id',
            'transaction_type',
            'result_code',
            'result_description',
            'mpesa_receipt_number',
            'callback_received',
            'related_saving',
            'related_loan',
            'related_instalment_number',
            'created_at',
            'updated_at',
        )
        read_only_fields = fields


class CallbackSerializer(serializers.ModelSerializer):
    class Meta:
        model = Callback
        fields = (
            'id',
            'transaction',
            'provider',
            'raw_payload',
            'processed',
            'processing_error',
            'received_at',
            'processed_at',
        )
        read_only_fields = (
            'id',
            'processed',
            'processing_error',
            'received_at',
            'processed_at',
        )
        extra_kwargs = {
            'raw_payload': {'write_only': True},
        }
