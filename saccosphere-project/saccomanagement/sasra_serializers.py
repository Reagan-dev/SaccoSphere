"""Serializers for SASRA regulatory return API responses."""

from rest_framework import serializers


class PARCategorySerializer(serializers.Serializer):
    """Serializer for individual PAR category data."""
    label = serializers.CharField()
    loan_count = serializers.IntegerField()
    outstanding_balance = serializers.CharField()
    provision_required = serializers.CharField()


class PARReturnSerializer(serializers.Serializer):
    """Serializer for Portfolio at Risk (PAR) return."""
    as_of_date = serializers.DateField()
    total_outstanding_book = serializers.CharField()
    categories = serializers.DictField(
        child=PARCategorySerializer(),
        help_text='PAR classification by SASRA risk category'
    )
    par30_ratio = serializers.CharField()
    par90_ratio = serializers.CharField()


class FinancialPositionAssetsSerializer(serializers.Serializer):
    """Serializer for assets in financial position return."""
    loans_outstanding = serializers.CharField()
    cash_balance = serializers.CharField()
    total_assets = serializers.CharField()


class FinancialPositionLiabilitiesSerializer(serializers.Serializer):
    """Serializer for liabilities in financial position return."""
    savings_by_type = serializers.DictField(
        child=serializers.CharField(),
        help_text='Savings breakdown by type (BOSA, FOSA, SHARE_CAPITAL)'
    )
    total_liabilities = serializers.CharField()


class FinancialPositionReturnSerializer(serializers.Serializer):
    """Serializer for statement of financial position return."""
    as_of_date = serializers.DateField()
    assets = FinancialPositionAssetsSerializer()
    liabilities = FinancialPositionLiabilitiesSerializer()


class MembershipReturnSerializer(serializers.Serializer):
    """Serializer for membership statistics return."""
    period_start = serializers.DateField()
    period_end = serializers.DateField()
    current_members_by_status = serializers.DictField(
        child=serializers.IntegerField(),
        help_text='Member counts by status'
    )
    total_current_members = serializers.IntegerField()
    new_registrations = serializers.IntegerField()
    exits = serializers.IntegerField()
