from django.urls import path

from .views import (
    ActivityFeedView,
    DashboardStateView,
    LoanComparisonView,
    PortfolioView,
    SACCOSwitcherView,
)


app_name = 'dashboard'

urlpatterns = [
    path('activity/', ActivityFeedView.as_view(), name='activity-feed'),
    path('loans/compare/', LoanComparisonView.as_view(), name='loan-compare'),
    path('portfolio/', PortfolioView.as_view(), name='portfolio'),
    path('saccos/', SACCOSwitcherView.as_view(), name='sacco-switcher'),
    path('state/', DashboardStateView.as_view(), name='state'),
]
