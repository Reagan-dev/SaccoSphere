from rest_framework import serializers

from .models import LedgerEntry


class LedgerEntrySerializer(serializers.ModelSerializer):
    """Serialize ledger entries as read-only records."""

    class Meta:
        model = LedgerEntry
        fields = '__all__'
        # DRF requires a list/tuple here, not the '__all__' string above -
        # spelled out explicitly so this stays correct if fields change.
        read_only_fields = (
            'id',
            'membership',
            'entry_type',
            'category',
            'amount',
            'reference',
            'description',
            'balance_after',
            'transaction',
            'created_at',
        )


class BalanceSerializer(serializers.Serializer):
    """Serialize the current ledger balance for one SACCO membership."""

    sacco_id = serializers.UUIDField()
    sacco_name = serializers.CharField()
    current_balance = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
    )
    as_of_date = serializers.DateField(allow_null=True)


class StatementEntrySerializer(serializers.Serializer):
    """Serialize one statement ledger row."""

    entry_type = serializers.CharField()
    category = serializers.CharField()
    amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    description = serializers.CharField()
    reference = serializers.CharField()
    balance_after = serializers.DecimalField(max_digits=12, decimal_places=2)
    created_at = serializers.DateTimeField()


class StatementSerializer(serializers.Serializer):
    """Serialize a member ledger statement."""

    member_name = serializers.CharField(allow_blank=True)
    member_number = serializers.CharField(allow_blank=True, allow_null=True)
    sacco_name = serializers.CharField()
    sacco_logo_url = serializers.CharField(allow_blank=True, allow_null=True)
    from_date = serializers.DateField()
    to_date = serializers.DateField()
    generated_at = serializers.DateTimeField()
    opening_balance = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
    )
    closing_balance = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
    )
    total_credits = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
    )
    total_debits = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
    )
    entries = StatementEntrySerializer(many=True)
    currency = serializers.CharField()
