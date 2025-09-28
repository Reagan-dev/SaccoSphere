from django.shortcuts import render
from rest_framework import viewsets, status, permissions
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated, AllowAny, IsAdminUser
from .serializers import UserSerializer, RegisterUserSerializer, LoginSerializer, logoutSerializer, SaccoSerializer, ProfileSerializer
from .models import User, Sacco, Profile


# accounts app views handling user registration, login, logout, sacco registration, and profile management.

# Standard helper function for response formatting
def standard_response(success, message, data = None, status_code = status.HTTP_200_OK, errors = None):

    return Response(
        {
            'success': success,
            'message': message,
            'data': data,
            'errors': errors,
        },
        status=status_code,
    )

class RegisterUserView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = RegisterUserSerializer(data=request.data)
        if serializer.is_valid():
            user = serializer.save()
            return standard_response(
                success=True,
                message="User registered successfully.",
                data=UserSerializer(user).data,
                status_code=status.HTTP_201_CREATED,
            )
        return standard_response(
            success=False,
            message="User registration failed.",
            errors=serializer.errors,
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    
class LoginView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = LoginSerializer(data=request.data, context={'request': request})
        if serializer.is_valid():
            tokens = serializer.save()
            return standard_response(
                success=True,
                message="User login successful.",
                data=tokens,
                status_code=status.HTTP_200_OK,
            )
        return standard_response(
            success=False,
            message="Login failed.",
            errors=serializer.errors,
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    
class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = logoutSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return standard_response(
                success=True,
                message="User logged out successfully.",
                status_code=status.HTTP_200_OK,
            )
        return standard_response(
            success=False,
            message="Logout failed.",
            errors=serializer.errors,
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    
class SaccoViewSet(viewsets.ModelViewSet):
    queryset = Sacco.objects.all()
    serializer_class = SaccoSerializer
    
    def get_permissions(self):
        if self.action in ['list', 'retrieve']:  # anyone can view/list
            return [permissions.AllowAny()]
        return [permissions.IsAdminUser()]  # admin required for create/update/delete

    def create(self, request, *args, **kwargs):
        if not request.user.is_staff:
            return standard_response(
                success=False,
                message="Only administrators can register a sacco.",
                status_code=status.HTTP_403_FORBIDDEN,
            )
        return super().create(request, *args, **kwargs)
    
    def update(self, request, *args, **kwargs):
        if not request.user.is_staff:
            return standard_response(
                success=False,
                message="Only administrators can update a sacco.",
                status_code=status.HTTP_403_FORBIDDEN,
            )
        return super().update(request, *args, **kwargs)
    
    def destroy(self, request, *args, **kwargs):
        if not request.user.is_staff:
            return standard_response(
                success=False,
                message="Only administrators can delete a sacco.",
                status_code=status.HTTP_403_FORBIDDEN,
            )
        return super().destroy(request, *args, **kwargs)
        



    
class ProfileViewSet(viewsets.ModelViewSet):
    queryset = Profile.objects.all()
    serializer_class = ProfileSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Profile.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    def create(self, request, *args, **kwargs):
        response = super().create(request, *args, **kwargs)
        return standard_response(
            success=True,
            message="Profile created successfully.",
            data=response.data,
            status_code=status.HTTP_201_CREATED,
        )

    def update(self, request, *args, **kwargs):
        response = super().update(request, *args, **kwargs)
        return standard_response(
            success=True,
            message="Profile updated successfully.",
            data=response.data,
            status_code=status.HTTP_200_OK,
        )

    def destroy(self, request, *args, **kwargs):
        super().destroy(request, *args, **kwargs)
        return standard_response(
            success=True,
            message="Profile deleted successfully.",
            status_code=status.HTTP_204_NO_CONTENT,
        )


