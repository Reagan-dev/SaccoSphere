from rest_framework import serializers

from .models import LedgerEntry


class LedgerEntrySerializer(serializers.ModelSerializer):
    """Serialize ledger entries as read-only records."""

    class Meta:
        model = LedgerEntry
        fields = '__all__'
        read_only_fields = fields


class BalanceSerializer(serializers.Serializer):
    """Serialize the current ledger balance for one SACCO membership."""

    sacco_id = serializers.UUIDField()
    sacco_name = serializers.CharField()
    current_balance = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
    )
    as_of_date = serializers.DateField(allow_null=True)
