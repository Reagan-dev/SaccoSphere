from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from drf_yasg import openapi
from drf_yasg.views import get_schema_view
from health.views import HealthCheckView, JobHealthView, ReadinessCheckView
from rest_framework import permissions
from rest_framework.authentication import SessionAuthentication


# The schema enumerates every endpoint on the platform, including
# staff/superadmin-only views and internal serializer field names, so it
# is not public: staff log in via /admin/login/ (session auth) and then
# browse /swagger/ or /redoc/ in the same browser session.
schema_view = get_schema_view(
    openapi.Info(
        title='SaccoSphere API',
        default_version='v1.0.0',
        description='SaccoSphere SACCO management API',
    ),
    public=False,
    authentication_classes=(SessionAuthentication,),
    permission_classes=(permissions.IsAdminUser,),
)

api_v1_patterns = [
    path('accounts/', include('accounts.urls')),
    path('members/', include('saccomembership.urls')),
    path(
        'management/',
        include('saccomanagement.urls', namespace='management'),
    ),
    path('saccomanagement/', include('saccomanagement.urls')),
    path('services/', include('services.urls')),
    path('payments/', include('payments.urls')),
    path('guarantors/', include('guarantor.urls')),
    path('notifications/', include('notifications.urls')),
    path('ledger/', include('ledger.urls')),
    path('dashboard/', include('dashboard.urls')),
    path('billing/', include('billing.urls')),
    path('health/', include('health.urls')),
]

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/v1/', include(api_v1_patterns)),
    # Deliberately duplicated at the unversioned root, in addition to
    # /api/v1/health/... above: orchestrators/uptime monitors (Railway,
    # Kubernetes) are typically configured against a bare /health/ path,
    # while API clients use the versioned one. Both resolve to the same
    # view classes, so there is no behavioural drift between them.
    path('health/', HealthCheckView.as_view(), name='health-check'),
    path(
        'health/ready/',
        ReadinessCheckView.as_view(),
        name='readiness-check',
    ),
    path('health/jobs/', JobHealthView.as_view(), name='job-health-check'),
    path(
        'swagger/',
        # Schema generation walks every installed viewset/serializer, so
        # it is not free; cache it briefly rather than regenerating on
        # every staff page load/navigation within drf-yasg's UI.
        schema_view.with_ui('swagger', cache_timeout=60),
        name='schema-swagger-ui',
    ),
    path(
        'redoc/',
        schema_view.with_ui('redoc', cache_timeout=60),
        name='schema-redoc',
    ),
]

if settings.DEBUG:
    urlpatterns += static(
        settings.MEDIA_URL,
        document_root=settings.MEDIA_ROOT,
    )
