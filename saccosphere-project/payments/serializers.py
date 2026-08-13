from decimal import Decimal

from rest_framework import serializers

from accounts.models import Sacco
from payments.fee_calculator import SaccoInvoiceFeeCalculator
from saccomembership.models import Membership
from services.models import Saving

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
        breakdown = SaccoInvoiceFeeCalculator().calculate(
            'deposit',
            net_amount,
        )

        data['net_amount'] = net_amount
        data['platform_fee'] = breakdown['platform_fee']
        data['gross_amount'] = breakdown['gross_amount']
        data['fee_rate'] = breakdown.get('fee_rate')
        return data

    def validate_membership(self, user):
        """Return True when the user may deposit into the target SACCO."""
        sacco = self.validated_data['sacco']
        return Membership.objects.filter(
            user=user,
            sacco=sacco,
            status=Membership.Status.APPROVED,
        ).exists()


class WithdrawalRequestSerializer(serializers.Serializer):
    """Validate a savings withdrawal and attach the fee breakdown."""

    phone_number = serializers.CharField(max_length=15)
    amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    sacco_id = serializers.PrimaryKeyRelatedField(
        source='sacco',
        queryset=Sacco.objects.all(),
    )
    saving_id = serializers.PrimaryKeyRelatedField(
        source='saving',
        queryset=Saving.objects.select_related(
            'membership',
            'membership__sacco',
        ),
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
        requested_amount = data['amount']
        breakdown = SaccoInvoiceFeeCalculator().calculate(
            'withdrawal',
            requested_amount,
        )

        if breakdown['net_amount'] <= Decimal('0.00'):
            raise serializers.ValidationError(
                {'amount': 'Amount must exceed the withdrawal fee.'}
            )

        data['requested_amount'] = requested_amount
        data['net_amount'] = breakdown['net_amount']
        data['platform_fee'] = breakdown['platform_fee']
        data['gross_amount'] = breakdown['gross_amount']
        data['fee_rate'] = breakdown.get('fee_rate')
        return data

    def validate_withdrawal_context(self, user):
        """Return (is_valid, detail) for membership and balance checks."""
        saving = self.validated_data['saving']
        sacco = self.validated_data['sacco']
        membership = saving.membership

        if membership.user_id != user.id or membership.sacco_id != sacco.id:
            return (
                False,
                'You can only withdraw from your own saving in this SACCO.',
            )

        if membership.status != Membership.Status.APPROVED:
            return (
                False,
                'Your SACCO membership must be approved before withdrawal.',
            )

        if saving.status != Saving.Status.ACTIVE:
            return False, 'Only active savings accounts can be withdrawn from.'

        if saving.amount < self.validated_data['gross_amount']:
            return False, 'Insufficient savings balance for this withdrawal.'

        return True, ''


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
            'gross_amount',
            'platform_fee',
            'fee_rate',
            'sacco',
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
