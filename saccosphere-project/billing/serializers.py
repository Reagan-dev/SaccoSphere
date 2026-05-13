"""Billing API serializers."""

from rest_framework import serializers

from billing.models import MonthlySaccoInvoice


class MonthlySaccoInvoiceSerializer(serializers.ModelSerializer):
    sacco_name = serializers.CharField(source='sacco.name', read_only=True)

    class Meta:
        model = MonthlySaccoInvoice
        fields = (
            'id',
            'sacco',
            'sacco_name',
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
        read_only_fields = fields
