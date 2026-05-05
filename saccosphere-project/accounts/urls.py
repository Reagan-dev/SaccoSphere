from django.urls import path
from rest_framework_simplejwt.views import (
    TokenObtainPairView,
    TokenRefreshView,
)

from .views import (
    LoginView,
    LogoutView,
    MeView,
    PasswordChangeView,
    RegisterView,
    SaccoDetailView,
    SaccoListView,
)


app_name = 'accounts'

urlpatterns = [
    path('register/', RegisterView.as_view(), name='register'),
    path('login/', LoginView.as_view(), name='login'),
    path('logout/', LogoutView.as_view(), name='logout'),
    path(
        'token/',
        TokenObtainPairView.as_view(),
        name='token-obtain-pair',
    ),
    path('token/refresh/', TokenRefreshView.as_view(), name='token-refresh'),
    path('me/', MeView.as_view(), name='me'),
    path(
        'password/change/',
        PasswordChangeView.as_view(),
        name='password-change',
    ),
    path('saccos/', SaccoListView.as_view(), name='sacco-list'),
    path('saccos/<uuid:id>/', SaccoDetailView.as_view(), name='sacco-detail'),
]
