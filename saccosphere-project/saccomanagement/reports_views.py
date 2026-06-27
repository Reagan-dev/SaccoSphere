"""SACCO admin reporting endpoints."""

from datetime import datetime
from decimal import Decimal

from django.db.models import Count, Sum
from django.db.models.functions import TruncMonth
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsSaccoAdmin
from payments.models import Transaction
from saccomembership.models import Membership
from services.models import Loan

from .mixins import SaccoScopedMixin


ZERO = Decimal('0.00')
REPORT_TYPES = {'loans', 'contributions', 'members'}


class SaccoReportView(SaccoScopedMixin, APIView):
    """
    Return SACCO-scoped operational reports.

    GET /api/v1/management/reports/?type=loans|contributions|members
    """

    permission_classes = [IsAuthenticated, IsSaccoAdmin]

    def get(self, request):
        response = self._set_sacco_context()
        if response:
            return response

        sacco = self.get_sacco_context()
        if sacco is None:
            return Response(
                {'detail': 'SACCO context is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        report_type = request.query_params.get('type')
        if report_type not in REPORT_TYPES:
            raise ValidationError(
                {
                    'type': (
                        'Required. Must be one of: loans, contributions, '
                        'members.'
                    ),
                },
            )

        from_date, to_date = self._parse_date_range(request)
        if report_type == 'loans':
            payload = self._loan_portfolio_report(sacco, from_date, to_date)
        elif report_type == 'contributions':
            payload = self._contributions_report(sacco, from_date, to_date)
        else:
            payload = self._members_report(sacco, from_date, to_date)

        return Response(
            {
                'success': True,
                'type': report_type,
                'sacco_id': str(sacco.id),
                'from_date': from_date.isoformat(),
                'to_date': to_date.isoformat(),
                'data': payload,
            },
            status=status.HTTP_200_OK,
        )

    def _parse_date_range(self, request):
        today = timezone.localdate()
        from_raw = request.query_params.get('from_date')
        to_raw = request.query_params.get('to_date')

        if from_raw:
            from_date = datetime.strptime(from_raw, '%Y-%m-%d').date()
        else:
            from_date = today.replace(day=1)

        if to_raw:
            to_date = datetime.strptime(to_raw, '%Y-%m-%d').date()
        else:
            to_date = today

        if from_date > to_date:
            raise ValidationError(
                {'from_date': 'from_date must be on or before to_date.'},
            )
        return from_date, to_date

    def _loan_portfolio_report(self, sacco, from_date, to_date):
        loans = Loan.objects.filter(
            membership__sacco=sacco,
            created_at__date__gte=from_date,
            created_at__date__lte=to_date,
        )
        status_breakdown = loans.values('status').annotate(
            count=Count('id'),
            total_amount=Sum('amount'),
        )
        return {
            'total_applications': loans.count(),
            'total_amount_requested': str(
                loans.aggregate(total=Sum('amount'))['total'] or ZERO,
            ),
            'approved_count': loans.filter(
                status=Loan.Status.APPROVED,
            ).count(),
            'active_count': loans.filter(status=Loan.Status.ACTIVE).count(),
            'rejected_count': loans.filter(
                status=Loan.Status.REJECTED,
            ).count(),
            'defaulted_count': loans.filter(
                status=Loan.Status.DEFAULTED,
            ).count(),
            'status_breakdown': [
                {
                    'status': row['status'],
                    'count': row['count'],
                    'total_amount': str(row['total_amount'] or ZERO),
                }
                for row in status_breakdown
            ],
        }

    def _contributions_report(self, sacco, from_date, to_date):
        contributions = Transaction.objects.filter(
            status=Transaction.Status.COMPLETED,
            transaction_type=Transaction.TransactionType.DEPOSIT,
            mpesa__related_saving__membership__sacco=sacco,
            created_at__date__gte=from_date,
            created_at__date__lte=to_date,
        )
        monthly = contributions.annotate(
            month=TruncMonth('created_at'),
        ).values('month').annotate(
            total_amount=Sum('amount'),
            transaction_count=Count('id'),
        ).order_by('month')

        return {
            'total_amount': str(
                contributions.aggregate(total=Sum('amount'))['total'] or ZERO,
            ),
            'transaction_count': contributions.count(),
            'by_month': [
                {
                    'month': row['month'].date().isoformat()
                    if row['month']
                    else None,
                    'total_amount': str(row['total_amount'] or ZERO),
                    'transaction_count': row['transaction_count'],
                }
                for row in monthly
            ],
        }

    def _members_report(self, sacco, from_date, to_date):
        memberships = Membership.objects.filter(sacco=sacco)
        period_memberships = memberships.filter(
            created_at__date__gte=from_date,
            created_at__date__lte=to_date,
        )
        monthly = period_memberships.annotate(
            month=TruncMonth('created_at'),
        ).values('month').annotate(
            new_members=Count('id'),
        ).order_by('month')

        return {
            'total_members': memberships.filter(
                status=Membership.Status.APPROVED,
            ).count(),
            'new_members_in_period': period_memberships.count(),
            'approved_in_period': period_memberships.filter(
                status=Membership.Status.APPROVED,
            ).count(),
            'pending_in_period': period_memberships.filter(
                status=Membership.Status.PENDING,
            ).count(),
            'growth_by_month': [
                {
                    'month': row['month'].date().isoformat()
                    if row['month']
                    else None,
                    'new_members': row['new_members'],
                }
                for row in monthly
            ],
        }
