from rest_framework import serializers
from .models import Service, Saving, Loan, Insurance


class ServiceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Service
        fields = ['id', 'name', 'description', 'created_at']
        read_only_fields = ['id', 'created_at']


class SavingSerializer(serializers.ModelSerializer):
    service = ServiceSerializer(read_only=True) 
    service_id = serializers.PrimaryKeyRelatedField(  
        queryset=Service.objects.all(),
        source='service',
        write_only=True
    )

    class Meta:
        model = Saving
        fields = ['id', 'member', 'amount', 'service', 'service_id', 'transaction_type', 'created_at']
        read_only_fields = ['id', 'created_at']


class LoanSerializer(serializers.ModelSerializer):
    service = ServiceSerializer(read_only=True)
    service_id = serializers.PrimaryKeyRelatedField(
        queryset=Service.objects.all(),
        source='service',
        write_only=True
    )

    class Meta:
        model = Loan
        fields = ['id', 'member', 'service', 'service_id', 'amount', 'interest_rate',
                  'duration_months', 'status', 'created_at']
        read_only_fields = ['id', 'created_at', 'status']


class InsuranceSerializer(serializers.ModelSerializer):
    service = ServiceSerializer(read_only=True)
    service_id = serializers.PrimaryKeyRelatedField(
        queryset=Service.objects.all(),
        source='service',
        write_only=True
    )

    class Meta:
        model = Insurance
        fields = ['id', 'member', 'service', 'service_id', 'policy_number',
                  'coverage_amount', 'premium', 'start_date', 'end_date', 'created_at']
        read_only_fields = ['id', 'created_at']
