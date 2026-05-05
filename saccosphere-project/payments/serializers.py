from rest_framework import serializers

from .models import Callback, MpesaTransaction, Transaction


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
