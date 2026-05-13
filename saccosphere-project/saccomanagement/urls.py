from django.urls import path

from accounts.views import AdminKYCQueueView, AdminKYCReviewView

from .role_views import RoleAssignView, RoleRevokeView, UserRolesView
from .views import (
    AdminLoanApprovalView,
    AdminMemberDetailView,
    AdminMemberListView,
    AdminSaccoStatsView,
    ApplicationReviewView,
)


app_name = 'saccomanagement'

urlpatterns = [
    # Role management
    path('roles/assign/', RoleAssignView.as_view(), name='role-assign'),
    path(
        'roles/<uuid:role_id>/',
        RoleRevokeView.as_view(),
        name='role-revoke',
    ),
    path('roles/', UserRolesView.as_view(), name='user-roles'),
    # Admin views
    path('members/', AdminMemberListView.as_view(), name='member-list'),
    path(
        'members/<uuid:membership_id>/',
        AdminMemberDetailView.as_view(),
        name='member-detail',
    ),
    path('stats/', AdminSaccoStatsView.as_view(), name='sacco-stats'),
    path(
        'applications/<uuid:id>/review/',
        ApplicationReviewView.as_view(),
        name='application-review',
    ),
    path('kyc/queue/', AdminKYCQueueView.as_view(), name='kyc-queue'),
    path(
        'kyc/<uuid:kyc_id>/review/',
        AdminKYCReviewView.as_view(),
        name='kyc-review',
    ),
    path(
        'loans/<uuid:id>/status/',
        AdminLoanApprovalView.as_view(),
        name='loan-approval',
    ),
]
