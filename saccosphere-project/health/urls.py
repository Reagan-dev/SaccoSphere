from django.urls import path

from .views import JobHealthView, LivenessView, ReadinessView


app_name = 'health'

urlpatterns = [
    path('', LivenessView.as_view(), name='liveness'),
    path('live/', LivenessView.as_view(), name='liveness-check'),
    path('ready/', ReadinessView.as_view(), name='readiness'),
    path('jobs/', JobHealthView.as_view(), name='job-health'),
]
