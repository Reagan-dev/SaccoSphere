"""Super Admin dashboard views for platform-wide visibility."""

from decimal import Decimal
from datetime import date, timedelta

from django.core.cache import cache
from django.db.models import (
    Count,
    Q,
    Sum,
    Value,
)
from django.db.models.functions import Coalesce
from django.utils import timezone

from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.generics import ListAPIView
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsSuperAdmin
from accounts.models import Sacco, User
from billing.models import PlatformRevenue, MonthlySaccoInvoice
from payments.models import Transaction, MpesaTransaction
from saccomembership.models import Membership

from .models import ComplianceFlag
from .superadmin_serializers import (
    AllMembersSerializer,
    AllSaccosSerializer,
    LiveTransactionSerializer,
    PlatformAlertSerializer,
    RevenueChartSerializer,
    SystemOverviewSerializer,
    TopSaccosSerializer,
)


ZERO = Decimal('0.00')


class SystemOverviewView(APIView):
    """
    Platform-wide overview statistics for Super Admin.

    GET /api/v1/management/superadmin/overview/
    """

    permission_classes = [IsAuthenticated, IsSuperAdmin]

    def get(self, request):
        now = timezone.now()
        current_month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        last_month_start = (current_month_start - timedelta(days=32)).replace(day=1)

        # Transaction volume MTD
        current_month_txns = Transaction.objects.filter(
            status=Transaction.Status.COMPLETED,
            created_at__gte=current_month_start,
        ).aggregate(
            total=Coalesce(Sum('amount'), Value(ZERO))
        )['total']

        last_month_txns = Transaction.objects.filter(
            status=Transaction.Status.COMPLETED,
            created_at__gte=last_month_start,
            created_at__lt=current_month_start,
        ).aggregate(
            total=Coalesce(Sum('amount'), Value(ZERO))
        )['total']

        volume_change_pct = None
        if last_month_txns > 0:
            volume_change_pct = ((current_month_txns - last_month_txns) / last_month_txns) * 100

        # Active SACCOs
        active_saccos_count = Sacco.objects.filter(is_active=True).count()
        new_saccos_this_month = Sacco.objects.filter(
            is_active=True,
            created_at__gte=current_month_start,
        ).count()

        # Total members
        total_members = Membership.objects.filter(
            status=Membership.Status.APPROVED,
        ).count()
        new_members_this_month = Membership.objects.filter(
            status=Membership.Status.APPROVED,
            approved_date__gte=current_month_start,
        ).count()

        # Platform revenue MTD
        platform_revenue_mtd = PlatformRevenue.objects.filter(
            recorded_at__gte=current_month_start,
        ).aggregate(
            total=Coalesce(Sum('amount'), Value(ZERO))
        )['total']

        # All systems operational
        critical_flags = ComplianceFlag.objects.filter(
            severity=ComplianceFlag.Severity.CRITICAL,
            status__in=[
                ComplianceFlag.Status.OPEN,
                ComplianceFlag.Status.INVESTIGATING,
            ],
        ).exists()
        all_systems_operational = not critical_flags

        data = {
            'platform_transaction_volume_mtd': current_month_txns,
            'platform_transaction_volume_change_pct': volume_change_pct,
            'active_saccos_count': active_saccos_count,
            'active_saccos_change_this_month': new_saccos_this_month,
            'total_members': total_members,
            'total_members_change_this_month': new_members_this_month,
            'platform_revenue_mtd': platform_revenue_mtd,
            'all_systems_operational': all_systems_operational,
        }

        serializer = SystemOverviewSerializer(data)
        return Response(serializer.data, status=status.HTTP_200_OK)


class PlatformRevenueChartView(APIView):
    """
    Monthly revenue chart for the last 12 months.

    GET /api/v1/management/superadmin/revenue-chart/
    """

    permission_classes = [IsAuthenticated, IsSuperAdmin]

    def get(self, request):
        now = timezone.now()
        months_data = []

        for i in range(12):
            month_start = (now.replace(day=1) - timedelta(days=32 * i)).replace(day=1)
            month_end = (month_start + timedelta(days=32)).replace(day=1) - timedelta(seconds=1)

            # SaaS fees from subscriptions
            saas_fees = PlatformRevenue.objects.filter(
                revenue_type=PlatformRevenue.RevenueType.SUBSCRIPTION,
                recorded_at__gte=month_start,
                recorded_at__lte=month_end,
            ).aggregate(
                total=Coalesce(Sum('amount'), Value(ZERO))
            )['total']

            # Transaction fees
            transaction_fees = PlatformRevenue.objects.filter(
                revenue_type=PlatformRevenue.RevenueType.TRANSACTION_FEE,
                recorded_at__gte=month_start,
                recorded_at__lte=month_end,
            ).aggregate(
                total=Coalesce(Sum('amount'), Value(ZERO))
            )['total']

            total_mrr = saas_fees + transaction_fees

            months_data.append({
                'month': month_start.strftime('%Y-%m'),
                'saas_fees': saas_fees,
                'transaction_fees': transaction_fees,
                'total_mrr': total_mrr,
            })

        months_data.reverse()

        serializer = RevenueChartSerializer(months_data, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


class TopSaccosView(APIView):
    """
    Top 10 SACCOs ranked by transaction volume this month.

    GET /api/v1/management/superadmin/top-saccos/
    """

    permission_classes = [IsAuthenticated, IsSuperAdmin]

    def get(self, request):
        now = timezone.now()
        current_month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        saccos = Sacco.objects.filter(is_active=True).annotate(
            member_count=Count(
                'saccomembership',
                filter=Q(saccomembership__status=Membership.Status.APPROVED),
            ),
            txn_volume_this_month=Coalesce(
                Sum(
                    'saccomembership__user__transactions__amount',
                    filter=Q(
                        saccomembership__user__transactions__status=Transaction.Status.COMPLETED,
                        saccomembership__user__transactions__created_at__gte=current_month_start,
                    ),
                ),
                Value(ZERO),
            ),
        ).order_by('-txn_volume_this_month')[:10]

        top_saccos = []
        for sacco in saccos:
            # Platform fee this month
            platform_fee = PlatformRevenue.objects.filter(
                sacco=sacco,
                recorded_at__gte=current_month_start,
            ).aggregate(
                total=Coalesce(Sum('amount'), Value(ZERO))
            )['total']

            # Health status
            critical_count = sacco.compliance_flags.filter(
                severity=ComplianceFlag.Severity.CRITICAL,
                status__in=[
                    ComplianceFlag.Status.OPEN,
                    ComplianceFlag.Status.INVESTIGATING,
                ],
            ).count()

            health_status = 'GOOD'
            if critical_count > 0:
                health_status = 'API_ISSUE'
            elif sacco.compliance_flags.filter(
                severity=ComplianceFlag.Severity.HIGH,
                status__in=[
                    ComplianceFlag.Status.OPEN,
                    ComplianceFlag.Status.INVESTIGATING,
                ],
            ).exists():
                health_status = 'REVIEW'

            top_saccos.append({
                'sacco_id': sacco.id,
                'sacco_name': sacco.name,
                'member_count': sacco.member_count or 0,
                'txn_volume_this_month': sacco.txn_volume_this_month or ZERO,
                'platform_fee_this_month': platform_fee,
                'health_status': health_status,
            })

        serializer = TopSaccosSerializer(top_saccos, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


class PlatformAlertsView(ListAPIView):
    """
    All open compliance flags ordered by severity.

    GET /api/v1/management/superadmin/alerts/
    """

    permission_classes = [IsAuthenticated, IsSuperAdmin]
    pagination_class = None

    def get_queryset(self):
        return ComplianceFlag.objects.filter(
            status__in=[
                ComplianceFlag.Status.OPEN,
                ComplianceFlag.Status.INVESTIGATING,
            ],
        ).select_related('sacco').order_by('-severity', '-created_at')

    def list(self, request, *args, **kwargs):
        queryset = self.get_queryset()
        alerts = []
        for flag in queryset:
            alerts.append({
                'sacco_name': flag.sacco.name,
                'flag_type': flag.flag_type,
                'description': flag.description,
                'severity': flag.severity,
                'created_at': flag.created_at,
            })

        serializer = PlatformAlertSerializer(alerts, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


class LiveTransactionFeedView(APIView):
    """
    Last 50 M-Pesa transactions across all SACCOs.

    GET /api/v1/management/superadmin/transactions/live/
    """

    permission_classes = [IsAuthenticated, IsSuperAdmin]

    def get(self, request):
        cache_key = 'live_transaction_feed'
        cached_data = cache.get(cache_key)

        if cached_data:
            return Response(cached_data, status=status.HTTP_200_OK)

        mpesa_txns = MpesaTransaction.objects.select_related(
            'transaction__user',
        ).order_by('-created_at')[:50]

        transactions = []
        for mpesa_txn in mpesa_txns:
            txn = mpesa_txn.transaction
            user = txn.user

            # Get SACCO name from membership
            membership = Membership.objects.filter(
                user=user,
                status=Membership.Status.APPROVED,
            ).first()
            sacco_name = membership.sacco.name if membership else 'Unknown'

            transactions.append({
                'sacco_name': sacco_name,
                'user_name': user.get_full_name(),
                'amount': txn.amount,
                'transaction_type': txn.transaction_type,
                'stk_status': mpesa_txn.result_code or 'PENDING',
                'created_at': mpesa_txn.created_at,
            })

        serializer = LiveTransactionSerializer(transactions, many=True)
        cache.set(cache_key, serializer.data, timeout=10)

        return Response(serializer.data, status=status.HTTP_200_OK)


class AllSaccosListView(ListAPIView):
    """
    All SACCOs with health status.

    GET /api/v1/management/superadmin/saccos/
    """

    permission_classes = [IsAuthenticated, IsSuperAdmin]
    serializer_class = AllSaccosSerializer
    pagination_class = None

    def get_queryset(self):
        return Sacco.objects.all().prefetch_related('compliance_flags')


class MemberPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 100


class AllMembersListView(ListAPIView):
    """
    All members across platform with pagination and filters.

    GET /api/v1/management/superadmin/members/
    Query params: ?sacco_id=, ?search=email/name
    """

    permission_classes = [IsAuthenticated, IsSuperAdmin]
    pagination_class = MemberPagination

    def get_queryset(self):
        queryset = Membership.objects.filter(
            status=Membership.Status.APPROVED,
        ).select_related('user', 'sacco')

        sacco_id = self.request.query_params.get('sacco_id')
        if sacco_id:
            queryset = queryset.filter(sacco_id=sacco_id)

        search = self.request.query_params.get('search')
        if search:
            queryset = queryset.filter(
                Q(user__email__icontains=search)
                | Q(user__first_name__icontains=search)
                | Q(user__last_name__icontains=search),
            )

        return queryset.order_by('-approved_date')

    def list(self, request, *args, **kwargs):
        queryset = self.get_queryset()
        page = self.paginate_queryset(queryset)

        members = []
        for membership in page:
            user = membership.user
            kyc_status = getattr(getattr(user, 'kyc', None), 'status', None)

            members.append({
                'id': user.id,
                'full_name': user.get_full_name(),
                'email': user.email,
                'phone_number': user.phone_number,
                'kyc_status': kyc_status,
                'member_since': membership.approved_date,
            })

        serializer = AllMembersSerializer(members, many=True)
        return self.get_paginated_response(serializer.data)
