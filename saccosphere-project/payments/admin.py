from django.contrib import admin

from .models import (
    Callback,
    MpesaIdempotencyRecord,
    MpesaTransaction,
    PaymentProvider,
    PlatformFee,
    Transaction,
)


@admin.register(PaymentProvider)
class PaymentProviderAdmin(admin.ModelAdmin):
    list_display = ('name', 'provider_type', 'is_active', 'created_at')
    list_filter = ('provider_type', 'is_active')
    search_fields = ('name',)


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = (
        'reference',
        'user',
        'transaction_type',
        'amount',
        'currency',
        'status',
        'created_at',
    )
    list_filter = ('transaction_type', 'status', 'currency', 'provider')
    search_fields = (
        'reference',
        'external_reference',
        'user__email',
        'description',
    )
    readonly_fields = ('created_at', 'updated_at')


@admin.register(Callback)
class CallbackAdmin(admin.ModelAdmin):
    list_display = ('provider', 'transaction', 'processed', 'received_at')
    list_filter = ('provider', 'processed', 'received_at')
    search_fields = (
        'provider__name',
        'transaction__reference',
        'processing_error',
    )
    readonly_fields = ('received_at', 'processed_at')


@admin.register(MpesaTransaction)
class MpesaTransactionAdmin(admin.ModelAdmin):
    list_display = (
        'phone_number',
        'transaction_type',
        'result_code',
        'mpesa_receipt_number',
        'callback_received',
        'created_at',
    )
    list_filter = ('transaction_type', 'callback_received', 'result_code')
    search_fields = (
        'phone_number',
        'checkout_request_id',
        'conversation_id',
        'mpesa_receipt_number',
    )
    readonly_fields = ('created_at', 'updated_at')


@admin.register(MpesaIdempotencyRecord)
class MpesaIdempotencyRecordAdmin(admin.ModelAdmin):
    list_display = ('checkout_request_id', 'processed_at')
    search_fields = ('checkout_request_id',)
    readonly_fields = ('processed_at',)


@admin.register(PlatformFee)
class PlatformFeeAdmin(admin.ModelAdmin):
    list_display = (
        'fee_type',
        'amount',
        'transaction',
        'invoice_number',
        'processed',
        'created_at',
    )
    list_filter = ('fee_type', 'processed')
    search_fields = ('invoice_number', 'transaction__reference')
    readonly_fields = ('created_at',)
