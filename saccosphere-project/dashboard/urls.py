from django.urls import path

from .views import DashboardStateView, PortfolioView


app_name = 'dashboard'

urlpatterns = [
    path('portfolio/', PortfolioView.as_view(), name='portfolio'),
    path('state/', DashboardStateView.as_view(), name='state'),
]
