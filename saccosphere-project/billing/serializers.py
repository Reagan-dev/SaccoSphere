"""Billing API serializers."""

from rest_framework import serializers

from billing.models import Invoice, InvoiceLineItem
from services.models import DisbursementAuditLog


class InvoiceLineItemSerializer(serializers.ModelSerializer):
    transaction_ref = serializers.CharField(
        source='transaction.reference',
        read_only=True,
    )

    class Meta:
        model = InvoiceLineItem
        fields = (
            'transaction_type',
            'gross_amount',
            'net_amount',
            'platform_fee',
            'fee_model',
            'rate_applied',
            'created_at',
            'transaction_ref',
        )
        read_only_fields = fields


class InvoiceListSerializer(serializers.ModelSerializer):
    days_overdue = serializers.SerializerMethodField()
    pdf_url = serializers.SerializerMethodField()

    class Meta:
        model = Invoice
        fields = (
            'id',
            'invoice_number',
            'billing_month',
            'total_amount',
            'line_items_count',
            'status',
            'sent_at',
            'due_date',
            'paid_at',
            'days_overdue',
            'pdf_url',
        )
        read_only_fields = fields

    def get_days_overdue(self, obj):
        return self._days_overdue(obj)

    def get_pdf_url(self, obj):
        return self._pdf_url(obj)

    def _days_overdue(self, obj):
        return self.context['invoice_helpers'].days_overdue(obj)

    def _pdf_url(self, obj):
        request = self.context.get('request')
        return self.context['invoice_helpers'].pdf_url(obj, request)


class InvoiceDetailSerializer(InvoiceListSerializer):
    line_items = serializers.SerializerMethodField()
    by_type = serializers.SerializerMethodField()

    class Meta(InvoiceListSerializer.Meta):
        fields = InvoiceListSerializer.Meta.fields + (
            'line_items',
            'by_type',
        )

    def get_line_items(self, obj):
        line_items = obj.invoicelineitem_set.all().order_by('created_at')
        return InvoiceLineItemSerializer(line_items, many=True).data

    def get_by_type(self, obj):
        return self.context['invoice_helpers'].by_type_summary(
            obj.invoicelineitem_set.all(),
        )


class RevenueSaccoSummarySerializer(serializers.Serializer):
    sacco_name = serializers.CharField()
    total_invoiced = serializers.DecimalField(
        max_digits=14,
        decimal_places=2,
    )
    total_paid = serializers.DecimalField(max_digits=14, decimal_places=2)
    outstanding = serializers.DecimalField(max_digits=14, decimal_places=2)


class RevenueMonthSummarySerializer(serializers.Serializer):
    month = serializers.DateField()
    total_invoiced = serializers.DecimalField(
        max_digits=14,
        decimal_places=2,
    )
    total_paid = serializers.DecimalField(max_digits=14, decimal_places=2)


class RevenueSummarySerializer(serializers.Serializer):
    total_revenue_all_time = serializers.DecimalField(
        max_digits=14,
        decimal_places=2,
    )
    revenue_this_month = serializers.DecimalField(
        max_digits=14,
        decimal_places=2,
    )
    revenue_last_month = serializers.DecimalField(
        max_digits=14,
        decimal_places=2,
    )
    outstanding_invoices_count = serializers.IntegerField()
    outstanding_invoices_total = serializers.DecimalField(
        max_digits=14,
        decimal_places=2,
    )
    overdue_invoices_count = serializers.IntegerField()
    suspended_saccos_count = serializers.IntegerField()
    by_sacco = RevenueSaccoSummarySerializer(many=True)
    by_month = RevenueMonthSummarySerializer(many=True)


class CurrentMonthPreviewSerializer(serializers.Serializer):
    billing_month = serializers.DateField()
    projected_invoice_total = serializers.DecimalField(
        max_digits=14,
        decimal_places=2,
    )
    transactions_count = serializers.IntegerField()
    by_type = serializers.DictField()
    note = serializers.CharField()


class DisbursementAuditLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = DisbursementAuditLog
        fields = (
            'event',
            'actor_role',
            'details',
            'mpesa_ref',
            'created_at',
        )
        read_only_fields = fields


class DisbursementAuditSerializer(serializers.Serializer):
    loan_id = serializers.UUIDField()
    current_status = serializers.CharField()
    mpesa_conversation_id = serializers.CharField(allow_blank=True)
    mpesa_transaction_id = serializers.CharField(allow_blank=True)
    audit_log = DisbursementAuditLogSerializer(many=True)
