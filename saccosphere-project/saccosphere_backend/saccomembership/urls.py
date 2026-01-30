from django.urls import path
from rest_framework.routers import DefaultRouter
from .views import MembershipViewSet, SaccoFieldViewSet 

router = DefaultRouter()
router.register(r'memberships', MembershipViewSet, basename='membership')
router.register(r'sacco_fields', SaccoFieldViewSet, basename='sacco-fields')

urlpatterns = router.urls