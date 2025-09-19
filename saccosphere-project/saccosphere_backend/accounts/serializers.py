from rest_framework import serializers
from .models import User, Sacco, Profile
from django.contrib.auth import authenticate
from rest_framework_simplejwt.tokens import RefreshToken


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
    
class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)

    def validate(self, data):
        email = data.get('email')
        password = data.get('password')

        if email and password:
            user = authenticate(request=self.context.get('request'), email=email, password=password)
            if not user:
                raise serializers.ValidationError("Invalid email or password.")
        else:
            raise serializers.ValidationError("Both email and password are required.")

        data['user'] = user
        return data

    def create(self, validated_data):
        user = validated_data['user']
        refresh = RefreshToken.for_user(user)
        return {
            'refresh': str(refresh),
            'access': str(refresh.access_token),
        }
    
class logoutSerializer(serializers.Serializer):
    refresh = serializers.CharField()

    def validate(self, data):
        self.token = data['refresh']
        return data

    def save(self, **kwargs):
        try:
            RefreshToken(self.token).blacklist()
        except Exception as e:
            self.fail('bad_token')



class SaccoSerializer(serializers.ModelSerializer):   
    class Meta:
        model = Sacco
        fields = '__all__'   
        read_only_fields = ['id', 'verified', 'created_at', 'updated_at']

       


class ProfileSerializer(serializers.ModelSerializer):   
    user = UserSerializer(read_only=True)  

    class Meta:
        model = Profile
        fields = ['user', 'phone_number', 'profile_picture', 'bio', 'created_at', 'updated_at']
        read_only_fields = ['created_at', 'updated_at']
