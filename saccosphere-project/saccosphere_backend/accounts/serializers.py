from rest_framework import serializers
from .models import User, Sacco, Profile


class UserSerializer(serializers.ModelSerializer):  
    class Meta:
        model = User
        fields = ['id', 'email', 'first_name', 'last_name', 'is_active', 'is_staff', 'date_joined']
        read_only_fields = ['id', 'is_active', 'is_staff', 'date_joined']



class RegisterUserSerializer(serializers.ModelSerializer):  
    password = serializers.CharField(write_only=True, min_length=8)   

    class Meta:
        model = User
        fields = ['id', 'email', 'first_name', 'last_name', 'password']

    
    def create(self, validated_data):
        user = User.objects.create_user(
            email=validated_data['email'],
            password=validated_data['password'],
            first_name=validated_data.get('first_name', ''),
            last_name=validated_data.get('last_name', ''),
        )
        return user



class SaccoSerializer(serializers.ModelSerializer):   
    class Meta:
        model = Sacco
        fields = '__all__'   
        read_only_fields = ['id', 'verified', 'created_at', 'updated_at']   


class ProfileSerializer(serializers.ModelSerializer):   
    user = UserSerializer(read_only=True)  

    class Meta:
        model = Profile
        
        fields = ['id', 'user', 'phone_number', 'profile_picture', 'bio', 'created_at', 'updated_at']
<<<<<<< HEAD
        read_only_fields = ['id', 'created_at', 'updated_at']
=======
        read_only_fields = ['id', 'created_at', 'updated_at']
>>>>>>> c3a3906a2794d59cc9fd8f89f88b2fce15d60e94
