"""SACCO admin dashboard data endpoints."""

from decimal import Decimal, ROUND_HALF_UP

from django.db.models import Count, DecimalField, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsSaccoAdmin
from payments.models import Transaction
from saccomembership.models import Membership
from services.models import Loan, Saving

from .mixins import SaccoScopedMixin


ZERO = Decimal('0.00')
TOTAL_AMOUNT_FIELD = DecimalField(max_digits=14, decimal_places=2)


class DisbursementsDashboardView(SaccoScopedMixin, APIView):
    """Return SACCO-scoped disbursement dashboard data."""

    permission_classes = [IsAuthenticated, IsSaccoAdmin]

    def get(self, request):
        """Return disbursement summaries and recent disbursed loans."""
        response = self._set_sacco_context()
        if response:
            return response

        sacco = self.get_sacco_context()
        if sacco is None:
            return Response(
                {'detail': 'SACCO context is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        today = timezone.localdate()
        disbursed_loans = self._get_disbursed_loans(sacco)
        disbursed_today = disbursed_loans.filter(disbursement_date=today)
        pending_disbursement = Loan.objects.filter(
            membership__sacco=sacco,
            status=Loan.Status.APPROVED,
            disbursement_date__isnull=True,
        )

        return Response(
            {
                'disbursed_today': self._loan_summary(
                    disbursed_today,
                    amount_field='disbursed_amount',
                ),
                'pending_disbursement': self._loan_summary(
                    pending_disbursement,
                    amount_field='amount',
                ),
                'total_disbursements': self._loan_summary(
                    disbursed_loans,
                    amount_field='disbursed_amount',
                ),
                'recent_disbursements': self._recent_disbursements(
                    disbursed_loans,
                ),
            },
            status=status.HTTP_200_OK,
        )

    def _get_disbursed_loans(self, sacco):
        disbursed_statuses = [
            Loan.Status.ACTIVE,
            Loan.Status.COMPLETED,
            'DISBURSED',
        ]
        return Loan.objects.filter(
            membership__sacco=sacco,
            status__in=disbursed_statuses,
            disbursement_date__isnull=False,
        ).select_related(
            'membership',
            'membership__user',
        )

    def _loan_summary(self, queryset, amount_field):
        return {
            'count': queryset.count(),
            'total_amount': queryset.aggregate(
                total=Coalesce(
                    Sum(amount_field),
                    Value(ZERO),
                    output_field=TOTAL_AMOUNT_FIELD,
                )
            )['total'],
        }

    def _recent_disbursements(self, queryset):
        recent_loans = queryset.order_by('-disbursement_date', '-updated_at')[
            :10
        ]
        return [
            {
                'member_name': loan.membership.user.get_full_name(),
                'member_number': loan.membership.member_number,
                'loan_id': str(loan.id),
                'amount': loan.disbursed_amount or loan.amount,
                'disbursed_at': loan.disbursement_date,
                'phone_number': loan.membership.user.phone_number,
            }
            for loan in recent_loans
        ]


class ContributionsDashboardView(SaccoScopedMixin, APIView):
    """Return SACCO-scoped contribution dashboard data."""

    permission_classes = [IsAuthenticated, IsSaccoAdmin]

    def get(self, request):
        """Return contribution summaries and recent savings deposits."""
        response = self._set_sacco_context()
        if response:
            return response

        sacco = self.get_sacco_context()
        if sacco is None:
            return Response(
                {'detail': 'SACCO context is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        today = timezone.localdate()
        month_start = today.replace(day=1)
        contributions = self._get_contributions(sacco)
        received_today = contributions.filter(created_at__date=today)
        received_this_month = contributions.filter(
            created_at__date__gte=month_start,
            created_at__date__lte=today,
        )
        expected_savings = self._get_expected_savings(sacco)
        contributing_member_ids = received_this_month.values_list(
            'mpesa__related_saving__membership_id',
            flat=True,
        ).distinct()
        missed_savings = expected_savings.exclude(
            membership_id__in=contributing_member_ids,
        )
        expected_summary = self._expected_summary(expected_savings)
        received_summary = self._transaction_summary(received_this_month)

        return Response(
            {
                'received_today': self._transaction_summary(received_today),
                'expected_this_month': expected_summary,
                'received_so_far_this_month': received_summary,
                'missed_overdue': self._expected_summary(missed_savings),
                'contribution_rate_pct': self._contribution_rate(
                    received_summary['total_amount'],
                    expected_summary['total_amount'],
                ),
                'recent_contributions': self._recent_contributions(
                    contributions,
                ),
            },
            status=status.HTTP_200_OK,
        )

    def _get_contributions(self, sacco):
        return Transaction.objects.filter(
            status=Transaction.Status.COMPLETED,
            transaction_type=Transaction.TransactionType.DEPOSIT,
            mpesa__related_saving__membership__sacco=sacco,
        ).select_related(
            'mpesa',
            'mpesa__related_saving',
            'mpesa__related_saving__membership',
            'mpesa__related_saving__membership__user',
            'mpesa__related_saving__savings_type',
        )

    def _get_expected_savings(self, sacco):
        return Saving.objects.filter(
            membership__sacco=sacco,
            membership__status=Membership.Status.APPROVED,
            status=Saving.Status.ACTIVE,
            savings_type__isnull=False,
        )

    def _transaction_summary(self, queryset):
        return {
            'count': queryset.count(),
            'total_amount': queryset.aggregate(
                total=Coalesce(
                    Sum('amount'),
                    Value(ZERO),
                    output_field=TOTAL_AMOUNT_FIELD,
                )
            )['total'],
        }

    def _expected_summary(self, queryset):
        return {
            'count': queryset.aggregate(
                total=Count('membership', distinct=True),
            )['total'],
            'total_amount': queryset.aggregate(
                total=Coalesce(
                    Sum('savings_type__minimum_contribution'),
                    Value(ZERO),
                    output_field=TOTAL_AMOUNT_FIELD,
                )
            )['total'],
        }

    def _contribution_rate(self, received_amount, expected_amount):
        if expected_amount == ZERO:
            return 0.0

        rate = (
            received_amount * Decimal('100') / expected_amount
        ).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        return float(rate)

    def _recent_contributions(self, queryset):
        recent_transactions = queryset.order_by('-created_at')[:10]
        return [
            {
                'member_name': self._member_name(transaction),
                'member_number': self._member_number(transaction),
                'amount': transaction.amount,
                'date': transaction.created_at,
                'savings_type': self._savings_type(transaction),
            }
            for transaction in recent_transactions
        ]

    def _member_name(self, transaction):
        saving = transaction.mpesa.related_saving
        return saving.membership.user.get_full_name()

    def _member_number(self, transaction):
        saving = transaction.mpesa.related_saving
        return saving.membership.member_number

    def _savings_type(self, transaction):
        saving = transaction.mpesa.related_saving
        if saving.savings_type is None:
            return 'General'
        return saving.savings_type.name
