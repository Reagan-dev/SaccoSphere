from rest_framework import serializers 
from .models import Membership
from accounts.serializers import SaccoSerializer, UserSerializer

class MembershipSerializer(serializers.ModelSerializer):
    class Meta:
        model = Membership
        fields = ['id', 'user', 'sacco', 'status', 'date_joined', 'is_active']
        read_only_fields = ['id', 'user', 'date_joined', 'is_active']

    def validate(self, data):
        request = self.context.get('request')
        user = request.user if request else data.get('user')

        if Membership.objects.filter(user=user, sacco=data['sacco']).exists():
            raise serializers.ValidationError(
                {"non_field_errors": ["This user is already a member of the Sacco."]}
            )
        return data

        
        
class MembershipDetailSerializer(serializers.ModelSerializer):
    sacco = SaccoSerializer(read_only = True) 
    user = UserSerializer(read_only =  True)
    
    class Meta:
        model = Membership
        fields = ['id', 'user', 'sacco', 'status', 'date_joined', 'is_active']
        read_only_fields = ['id', 'date_joined','is_active']       
            