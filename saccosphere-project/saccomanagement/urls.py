from django.urls import path

from accounts.views import AdminKYCQueueView, AdminKYCReviewView
from guarantor.external_views import (
    ExternalGuarantorAdminListView,
    ExternalGuarantorAdminReviewView,
)
from services.views import LiquidityStatusView

from .admin_views import AdminLoanApprovalView, LoanApprovalListView
from .dashboard_views import (
    ContributionsDashboardView,
    DisbursementsDashboardView,
)
from .import_views import MemberImportCreateView, MemberImportStatusView
from .reports_views import SaccoReportView
from .role_views import RoleAssignView, RoleRevokeView, UserRolesView
from .settings_views import SaccoSettingsView
from .superadmin_views import (
    AllMembersListView,
    AllSaccosListView,
    LiveTransactionFeedView,
    PlatformAlertsView,
    PlatformRevenueChartView,
    SystemOverviewView,
    TopSaccosView,
)
from .sasra_reports import SASRAReturnView
from .views import (
    AuditLogListView,
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
    path('audit-logs/', AuditLogListView.as_view(), name='audit-log-list'),
    path(
        'dashboard/disbursements/',
        DisbursementsDashboardView.as_view(),
        name='dashboard-disbursements',
    ),
    path(
        'dashboard/contributions/',
        ContributionsDashboardView.as_view(),
        name='dashboard-contributions',
    ),
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
        'loans/approvals/',
        LoanApprovalListView.as_view(),
        name='loan-approval-list',
    ),
    path(
        'loans/<uuid:id>/status/',
        AdminLoanApprovalView.as_view(),
        name='loan-approval',
    ),
    path('reports/', SaccoReportView.as_view(), name='sacco-reports'),
    path(
        'reports/sasra/',
        SASRAReturnView.as_view(),
        name='sasra-returns',
    ),
    path('settings/', SaccoSettingsView.as_view(), name='sacco-settings'),
    path(
        'liquidity/',
        LiquidityStatusView.as_view(),
        name='liquidity-status',
    ),
    path(
        'external-guarantors/',
        ExternalGuarantorAdminListView.as_view(),
        name='external-guarantor-list',
    ),
    path(
        'external-guarantors/<uuid:id>/review/',
        ExternalGuarantorAdminReviewView.as_view(),
        name='external-guarantor-review',
    ),
    path(
        'import/',
        MemberImportCreateView.as_view(),
        name='member-import',
    ),
    path(
        'import/<uuid:job_id>/',
        MemberImportStatusView.as_view(),
        name='member-import-status',
    ),
    # Super Admin views
    path(
        'superadmin/overview/',
        SystemOverviewView.as_view(),
        name='superadmin-overview',
    ),
    path(
        'superadmin/revenue-chart/',
        PlatformRevenueChartView.as_view(),
        name='superadmin-revenue-chart',
    ),
    path(
        'superadmin/top-saccos/',
        TopSaccosView.as_view(),
        name='superadmin-top-saccos',
    ),
    path(
        'superadmin/alerts/',
        PlatformAlertsView.as_view(),
        name='superadmin-alerts',
    ),
    path(
        'superadmin/transactions/live/',
        LiveTransactionFeedView.as_view(),
        name='superadmin-live-transactions',
    ),
    path(
        'superadmin/saccos/',
        AllSaccosListView.as_view(),
        name='superadmin-saccos',
    ),
    path(
        'superadmin/members/',
        AllMembersListView.as_view(),
        name='superadmin-members',
    ),
]
