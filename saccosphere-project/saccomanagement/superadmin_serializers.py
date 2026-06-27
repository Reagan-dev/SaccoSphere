"""Serializers for Super Admin dashboard views."""

from decimal import Decimal

from rest_framework import serializers

from accounts.models import Sacco, User
from saccomembership.models import Membership


class SystemOverviewSerializer(serializers.Serializer):
    """Serializer for platform-wide overview statistics."""

    platform_transaction_volume_mtd = serializers.DecimalField(
        max_digits=14,
        decimal_places=2,
    )
    platform_transaction_volume_change_pct = serializers.DecimalField(
        max_digits=5,
        decimal_places=2,
        allow_null=True,
    )
    active_saccos_count = serializers.IntegerField()
    active_saccos_change_this_month = serializers.IntegerField()
    total_members = serializers.IntegerField()
    total_members_change_this_month = serializers.IntegerField()
    platform_revenue_mtd = serializers.DecimalField(
        max_digits=14,
        decimal_places=2,
    )
    all_systems_operational = serializers.BooleanField()


class RevenueChartSerializer(serializers.Serializer):
    """Serializer for monthly revenue chart data."""

    month = serializers.CharField(max_length=7)  # YYYY-MM
    saas_fees = serializers.DecimalField(max_digits=14, decimal_places=2)
    transaction_fees = serializers.DecimalField(max_digits=14, decimal_places=2)
    total_mrr = serializers.DecimalField(max_digits=14, decimal_places=2)


class TopSaccosSerializer(serializers.Serializer):
    """Serializer for top SACCOs by transaction volume."""

    sacco_id = serializers.UUIDField()
    sacco_name = serializers.CharField()
    member_count = serializers.IntegerField()
    txn_volume_this_month = serializers.DecimalField(
        max_digits=14,
        decimal_places=2,
    )
    platform_fee_this_month = serializers.DecimalField(
        max_digits=14,
        decimal_places=2,
    )
    health_status = serializers.CharField()


class PlatformAlertSerializer(serializers.Serializer):
    """Serializer for compliance flag alerts."""

    sacco_name = serializers.CharField()
    flag_type = serializers.CharField()
    description = serializers.CharField()
    severity = serializers.CharField()
    created_at = serializers.DateTimeField()


class LiveTransactionSerializer(serializers.Serializer):
    """Serializer for live transaction feed."""

    sacco_name = serializers.CharField()
    user_name = serializers.CharField()
    amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    transaction_type = serializers.CharField()
    stk_status = serializers.CharField()
    created_at = serializers.DateTimeField()


class AllSaccosSerializer(serializers.ModelSerializer):
    """Serializer for all SACCOs list."""

    member_count = serializers.SerializerMethodField()
    health_status = serializers.SerializerMethodField()
    last_transaction_at = serializers.SerializerMethodField()

    class Meta:
        model = Sacco
        fields = (
            'id',
            'name',
            'member_count',
            'is_active',
            'created_at',
            'health_status',
            'last_transaction_at',
        )

    def get_member_count(self, obj):
        return Membership.objects.filter(
            sacco=obj,
            status=Membership.Status.APPROVED,
        ).count()

    def get_health_status(self, obj):
        from .models import ComplianceFlag

        critical_count = obj.compliance_flags.filter(
            severity=ComplianceFlag.Severity.CRITICAL,
            status__in=[
                ComplianceFlag.Status.OPEN,
                ComplianceFlag.Status.INVESTIGATING,
            ],
        ).count()

        if critical_count > 0:
            return 'API_ISSUE'
        return 'GOOD'

    def get_last_transaction_at(self, obj):
        from payments.models import Transaction

        last_txn = Transaction.objects.filter(
            user__membership__sacco=obj,
            status=Transaction.Status.COMPLETED,
        ).order_by('-created_at').first()

        return last_txn.created_at if last_txn else None


class AllMembersSerializer(serializers.Serializer):
    """Serializer for all members list."""

    id = serializers.UUIDField()
    full_name = serializers.CharField()
    email = serializers.EmailField()
    phone_number = serializers.CharField(allow_null=True)
    kyc_status = serializers.CharField(allow_null=True)
    member_since = serializers.DateField()
