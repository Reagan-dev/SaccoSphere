from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from drf_yasg import openapi
from drf_yasg.views import get_schema_view
from health.views import HealthCheckView, ReadinessCheckView
from rest_framework import permissions


schema_view = get_schema_view(
    openapi.Info(
        title='SaccoSphere API',
        default_version='v1.0.0',
        description='SaccoSphere SACCO management API',
    ),
    public=True,
    permission_classes=(permissions.AllowAny,),
)

api_v1_patterns = [
    path('accounts/', include('accounts.urls')),
    path('members/', include('saccomembership.urls')),
    path('saccomanagement/', include('saccomanagement.urls')),
    path('services/', include('services.urls')),
    path('payments/', include('payments.urls')),
    path('notifications/', include('notifications.urls')),
    path('ledger/', include('ledger.urls')),
    path('dashboard/', include('dashboard.urls')),
    path('billing/', include('billing.urls')),
    path('health/', include('health.urls')),
]

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/v1/', include(api_v1_patterns)),
    path('health/', HealthCheckView.as_view(), name='health-check'),
    path(
        'health/ready/',
        ReadinessCheckView.as_view(),
        name='readiness-check',
    ),
    path(
        'swagger/',
        schema_view.with_ui('swagger', cache_timeout=0),
        name='schema-swagger-ui',
    ),
    path(
        'redoc/',
        schema_view.with_ui('redoc', cache_timeout=0),
        name='schema-redoc',
    ),
]

if settings.DEBUG:
    urlpatterns += static(
        settings.MEDIA_URL,
        document_root=settings.MEDIA_ROOT,
    )
