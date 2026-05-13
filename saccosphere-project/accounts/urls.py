from django.urls import path
from rest_framework_simplejwt.views import (
    TokenObtainPairView,
    TokenRefreshView,
)

from .views import (
    KYCSubmitIDView,
    KYCStatusView,
    KYCUploadView,
    LoginView,
    LogoutView,
    MeView,
    OTPSendView,
    OTPVerifyView,
    OTPResendView,
    PasswordChangeView,
    PasswordResetRequestView,
    PasswordResetConfirmView,
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
    path('kyc/submit-id/', KYCSubmitIDView.as_view(), name='kyc-submit-id'),
    path('kyc/upload/', KYCUploadView.as_view(), name='kyc-upload'),
    path('kyc/status/', KYCStatusView.as_view(), name='kyc-status'),
    path(
        'password/change/',
        PasswordChangeView.as_view(),
        name='password-change',
    ),
    path('saccos/', SaccoListView.as_view(), name='sacco-list'),
    path('saccos/<uuid:id>/', SaccoDetailView.as_view(), name='sacco-detail'),
    # OTP endpoints
    path('otp/send/', OTPSendView.as_view(), name='otp-send'),
    path('otp/verify/', OTPVerifyView.as_view(), name='otp-verify'),
    path('otp/resend/', OTPResendView.as_view(), name='otp-resend'),
    # Password reset endpoints
    path('password/reset/', PasswordResetRequestView.as_view(), name='password-reset'),
    path('password/reset/confirm/', PasswordResetConfirmView.as_view(), name='password-reset-confirm'),
]
