from django.contrib import admin

from .models import (
    Invoice,
    InvoiceLineItem,
    InvoicePayment,
    MonthlySaccoInvoice,
    PlatformRevenue,
    SaccoSubscription,
)


class FinancialRecordAdminMixin:
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(SaccoSubscription)
class SaccoSubscriptionAdmin(FinancialRecordAdminMixin, admin.ModelAdmin):
    list_display = (
        'sacco',
        'plan',
        'status',
        'monthly_fee',
        'starts_at',
        'ends_at',
        'updated_at',
    )
    list_filter = ('plan', 'status', 'sacco')
    search_fields = ('sacco__name', 'sacco__registration_number')
    autocomplete_fields = ('sacco',)
    readonly_fields = (
        'id',
        'sacco',
        'plan',
        'status',
        'monthly_fee',
        'starts_at',
        'ends_at',
        'created_at',
        'updated_at',
    )
    list_select_related = ('sacco',)
    ordering = ('sacco__name',)
    fieldsets = (
        (
            None,
            {
                'fields': (
                    'sacco',
                    'plan',
                    'status',
                    'monthly_fee',
                    'starts_at',
                    'ends_at',
                ),
            },
        ),
        (
            'Audit',
            {
                'classes': ('collapse',),
                'fields': ('id', 'created_at', 'updated_at'),
            },
        ),
    )


@admin.register(PlatformRevenue)
class PlatformRevenueAdmin(FinancialRecordAdminMixin, admin.ModelAdmin):
    list_display = (
        'sacco',
        'transaction',
        'revenue_type',
        'amount',
        'currency',
        'is_collected',
        'recorded_at',
    )
    list_filter = ('revenue_type', 'sacco', 'is_collected', 'recorded_at')
    search_fields = (
        'sacco__name',
        'transaction__reference',
        'transaction__external_reference',
        'description',
    )
    autocomplete_fields = ('sacco', 'transaction')
    readonly_fields = (
        'id',
        'sacco',
        'transaction',
        'revenue_type',
        'amount',
        'currency',
        'description',
        'is_collected',
        'recorded_at',
    )
    list_select_related = ('sacco', 'transaction')
    list_per_page = 50
    ordering = ('-recorded_at',)
    fieldsets = (
        (
            None,
            {
                'fields': (
                    'sacco',
                    'transaction',
                    'revenue_type',
                    'amount',
                    'currency',
                    'is_collected',
                ),
            },
        ),
        (
            'Details and audit',
            {
                'classes': ('collapse',),
                'fields': ('description', 'id', 'recorded_at'),
            },
        ),
    )


@admin.register(MonthlySaccoInvoice)
class MonthlySaccoInvoiceAdmin(
    FinancialRecordAdminMixin,
    admin.ModelAdmin,
):
    list_display = (
        'sacco',
        'period_start',
        'period_end',
        'amount_due',
        'status',
        'due_date',
        'created_at',
    )
    list_filter = ('status', 'sacco', 'period_start', 'period_end')
    search_fields = ('sacco__name', 'sacco__registration_number')
    autocomplete_fields = ('sacco',)
    readonly_fields = (
        'id',
        'sacco',
        'period_start',
        'period_end',
        'amount_due',
        'currency',
        'status',
        'report_payload',
        'sent_at',
        'due_date',
        'created_at',
        'updated_at',
    )
    list_select_related = ('sacco',)
    list_per_page = 50
    ordering = ('-created_at',)
    fieldsets = (
        (
            None,
            {
                'fields': (
                    'sacco',
                    'period_start',
                    'period_end',
                    'amount_due',
                    'currency',
                    'status',
                    'due_date',
                    'sent_at',
                ),
            },
        ),
        (
            'Report payload and audit',
            {
                'classes': ('collapse',),
                'fields': ('report_payload', 'id', 'created_at', 'updated_at'),
            },
        ),
    )


@admin.register(Invoice)
class InvoiceAdmin(FinancialRecordAdminMixin, admin.ModelAdmin):
    list_display = (
        'invoice_number',
        'sacco',
        'billing_month',
        'total_amount',
        'status',
        'due_date',
        'created_at',
    )
    list_filter = ('status', 'sacco', 'billing_month', 'created_at')
    search_fields = (
        'invoice_number',
        'sacco__name',
        'payment_reference',
    )
    autocomplete_fields = ('sacco',)
    readonly_fields = (
        'id',
        'sacco',
        'invoice_number',
        'billing_month',
        'total_amount',
        'line_items_count',
        'status',
        'sent_at',
        'due_date',
        'paid_at',
        'pdf_path',
        'payment_reference',
        'notes',
        'created_at',
        'updated_at',
    )
    list_select_related = ('sacco',)
    list_per_page = 50
    ordering = ('-created_at',)
    fieldsets = (
        (
            None,
            {
                'fields': (
                    'sacco',
                    'invoice_number',
                    'billing_month',
                    'total_amount',
                    'line_items_count',
                    'status',
                ),
            },
        ),
        (
            'Payment details',
            {
                'fields': (
                    'due_date',
                    'sent_at',
                    'paid_at',
                    'payment_reference',
                ),
            },
        ),
        (
            'Files, notes, and audit',
            {
                'classes': ('collapse',),
                'fields': (
                    'pdf_path',
                    'notes',
                    'id',
                    'created_at',
                    'updated_at',
                ),
            },
        ),
    )


@admin.register(InvoiceLineItem)
class InvoiceLineItemAdmin(FinancialRecordAdminMixin, admin.ModelAdmin):
    list_display = (
        'sacco',
        'transaction',
        'transaction_type',
        'platform_fee',
        'billing_month',
        'invoiced',
        'created_at',
    )
    list_filter = (
        'transaction_type',
        'sacco',
        'invoiced',
        'billing_month',
        'created_at',
    )
    search_fields = (
        'sacco__name',
        'transaction__reference',
        'transaction__external_reference',
        'invoice__invoice_number',
        'fee_model',
        'rate_applied',
    )
    autocomplete_fields = ('sacco', 'transaction', 'invoice')
    readonly_fields = (
        'id',
        'sacco',
        'transaction',
        'transaction_type',
        'gross_amount',
        'net_amount',
        'platform_fee',
        'fee_model',
        'rate_applied',
        'tier_applied',
        'billing_month',
        'invoiced',
        'invoice',
        'created_at',
    )
    list_select_related = ('sacco', 'transaction', 'invoice')
    list_per_page = 50
    ordering = ('-created_at',)
    fieldsets = (
        (
            None,
            {
                'fields': (
                    'sacco',
                    'transaction',
                    'transaction_type',
                    'billing_month',
                    'invoice',
                    'invoiced',
                ),
            },
        ),
        (
            'Amounts',
            {
                'fields': (
                    'gross_amount',
                    'net_amount',
                    'platform_fee',
                ),
            },
        ),
        (
            'Fee calculation and audit',
            {
                'classes': ('collapse',),
                'fields': (
                    'fee_model',
                    'rate_applied',
                    'tier_applied',
                    'id',
                    'created_at',
                ),
            },
        ),
    )


@admin.register(InvoicePayment)
class InvoicePaymentAdmin(FinancialRecordAdminMixin, admin.ModelAdmin):
    list_display = (
        'invoice',
        'amount',
        'payment_method',
        'payment_ref',
        'recorded_by',
        'recorded_at',
    )
    list_filter = ('payment_method', 'recorded_at')
    search_fields = (
        'invoice__invoice_number',
        'invoice__sacco__name',
        'payment_ref',
        'recorded_by__email',
    )
    autocomplete_fields = ('invoice', 'recorded_by')
    readonly_fields = (
        'id',
        'invoice',
        'amount',
        'payment_method',
        'payment_ref',
        'recorded_by',
        'recorded_at',
        'notes',
    )
    list_select_related = ('invoice', 'recorded_by')
    list_per_page = 50
    ordering = ('-recorded_at',)
    fieldsets = (
        (
            None,
            {
                'fields': (
                    'invoice',
                    'amount',
                    'payment_method',
                    'payment_ref',
                    'recorded_by',
                ),
            },
        ),
        (
            'Notes and audit',
            {
                'classes': ('collapse',),
                'fields': ('notes', 'id', 'recorded_at'),
            },
        ),
    )
