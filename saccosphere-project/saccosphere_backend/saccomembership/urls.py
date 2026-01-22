from rest_framework.routers import DefaultRouter
from .views import MembershipViewSet, join_sacco
from django.urls import path, include

router = DefaultRouter()
router.register(r'memberships', MembershipViewSet, basename='membership')

urlpatterns = [
    path('join_sacco/<int:sacco_id>/', join_sacco, name='join_sacco'),
    path('', include(router.urls)),
]
