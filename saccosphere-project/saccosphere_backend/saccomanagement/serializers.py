from rest_framework import serializers  
from .models import Management
from accounts.serializers import SaccoSerializer

class ManagementSerializer(serializers.ModelSerializer):
    class Meta:
        model = Management
        fields =  ['id', 'sacco', 'management']
        read_only_fields = ['id']
        
class ManagementDetailSerializer(serializers.ModelSerializer):
    sacco = SaccoSerializer(read_only=True)
    
    class Meta:
        model = Management
        fields = ['id', 'sacco', 'management']
        read_only_fields = ['id']   