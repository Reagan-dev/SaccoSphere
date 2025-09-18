from rest_framework.routers import DefaultRouter
from .views import ManagementViewSet

router = DefaultRouter()
router.register(r'managements', ManagementViewSet, basename='management')

urlpatterns = router.urls
