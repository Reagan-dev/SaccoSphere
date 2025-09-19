from rest_framework.routers import DefaultRouter
from .views import PaymentProviderViewSet, TransactionViewSet, CallbackViewSet

router = DefaultRouter()
router.register(r'providers', PaymentProviderViewSet, basename='provider')
router.register(r'transactions', TransactionViewSet, basename='transaction')
router.register(r'callbacks', CallbackViewSet, basename='callback')

urlpatterns = router.urls