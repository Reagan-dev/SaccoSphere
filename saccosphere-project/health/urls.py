from django.urls import path

from .views import HealthCheckView, ReadinessCheckView


app_name = 'health'

urlpatterns = [
    path('', HealthCheckView.as_view(), name='health-check'),
    path('ready/', ReadinessCheckView.as_view(), name='readiness-check'),
]
