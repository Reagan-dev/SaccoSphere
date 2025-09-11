from rest_framework import serializers 
from .models import PaymentProvider, Transaction, Callback

class PaymentProviderSerializer(serializers.ModelSerializer):
    api_key = serializers.CharField(write_only=True, required=False)
    api_secret = serializers.CharField(write_only=True, required=False)
    
    class Meta:
        model = PaymentProvider
        fields = ['id', 'name', 'api_key','api_secret' ,'callback_url', 'is_active', 'created_at']
        read_only_fields = ['id', 'created_at']
        
class TransactionSerializer(serializers.ModelSerializer):
    provider = PaymentProviderSerializer(read_only=True)

    class Meta:
        model = Transaction
        fields = ['id', 'user', 'provider', 'amount', 'currency', 'status', 'reference', 'provider_reference', 'description', 'created_at', 'updated_at']
        read_only_fields = ['id', 'status', 'created_at', 'updated_at']
        
        
class CallbackSerializer(serializers.ModelSerializer):
    class Meta:
        model = Callback
        fields = ['id', 'transaction', 'provider', 'payload', 'received_at', 'processed']
        read_only_fields = ['id', 'received_at', 'processed']
        
                           