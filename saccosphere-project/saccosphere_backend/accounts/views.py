from django.shortcuts import render
from rest_framework import viewsets, status, permissions
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated, AllowAny
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
    permission_classes = [permissions.IsAuthenticatedOrReadOnly]

    def perform_create(self, serializer):
        serializer.save()

    def create(self, request, *args, **kwargs):
        response = super().create(request, *args, **kwargs)
        return standard_response(
            success=True,
            message="Sacco created successfully.",
            data=response.data,
            status_code=status.HTTP_201_CREATED,
        )

    def update(self, request, *args, **kwargs):
        response = super().update(request, *args, **kwargs)
        return standard_response(
            success=True,
            message="Sacco updated successfully.",
            data=response.data,
            status_code=status.HTTP_200_OK,
        )

    def destroy(self, request, *args, **kwargs):
        super().destroy(request, *args, **kwargs)
        return standard_response(
            success=True,
            message="Sacco deleted successfully.",
            status_code=status.HTTP_204_NO_CONTENT,
        )
    
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


