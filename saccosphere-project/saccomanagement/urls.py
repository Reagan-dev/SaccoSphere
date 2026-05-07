from django.urls import path

from .role_views import RoleAssignView, RoleRevokeView, UserRolesView
from .views import AdminMemberListView, AdminLoanApprovalView


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
        'loans/<uuid:id>/status/',
        AdminLoanApprovalView.as_view(),
        name='loan-approval',
    ),
]
