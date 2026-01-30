from django.urls import path
from rest_framework.routers import DefaultRouter
from .views import MembershipViewSet, sacco_fields  

router = DefaultRouter()
router.register(r'memberships', MembershipViewSet, basename='membership')

urlpatterns = [
    path('saccos/<int:sacco_id>/fields/', sacco_fields, name='sacco_fields'),
]

urlpatterns += router.urls
