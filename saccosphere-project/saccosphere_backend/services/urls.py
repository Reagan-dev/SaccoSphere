from rest_framework.routers import DefaultRouter
from .views import ServiceViewSet, SavingViewSet, LoanViewSet, InsuranceViewSet

router = DefaultRouter()
router.register(r'services', ServiceViewSet, basename='service')
router.register(r'savings', SavingViewSet, basename='saving')
router.register(r'loans', LoanViewSet, basename='loan')
router.register(r'insurances', InsuranceViewSet, basename='insurance')

urlpatterns = router.urls