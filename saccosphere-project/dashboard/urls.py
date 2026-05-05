from django.urls import path

from .views import (
    DashboardOverviewView,
    MemberDashboardView,
    SaccoDashboardView,
)


app_name = 'dashboard'

urlpatterns = [
    path('', DashboardOverviewView.as_view(), name='overview'),
    path('member/', MemberDashboardView.as_view(), name='member-dashboard'),
    path('sacco/', SaccoDashboardView.as_view(), name='sacco-dashboard'),
]
