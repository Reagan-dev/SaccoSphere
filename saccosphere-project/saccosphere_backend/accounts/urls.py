from rest_framework.routers import DefaultRouter
from .views import RegisterUserView, LoginView, LogoutView, SaccoViewSet, ProfileViewSet
from django.urls import path

router = DefaultRouter()
router.register(r'saccos', SaccoViewSet, basename='sacco')
router.register(r'profiles', ProfileViewSet, basename='profile')

urlpatterns = [
    path('register/', RegisterUserView.as_view(), name='register'),
    path('login/', LoginView.as_view(), name='login'),
    path('logout/', LogoutView.as_view(), name='logout'),
]

urlpatterns += router.urls
