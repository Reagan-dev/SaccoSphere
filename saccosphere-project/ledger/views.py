from rest_framework.exceptions import ValidationError
from rest_framework.generics import ListAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from config.pagination import FinancialPagination
from saccomembership.models import Membership

from .engines.balance_calculator import get_running_balance
from .models import LedgerEntry
from .serializers import BalanceSerializer, LedgerEntrySerializer


class LedgerEntryListView(ListAPIView):
    """List ledger entries for the user's membership in a SACCO."""

    serializer_class = LedgerEntrySerializer
    permission_classes = [IsAuthenticated]
    pagination_class = FinancialPagination

    def get_queryset(self):
        membership = self._get_membership()
        queryset = LedgerEntry.objects.filter(
            membership=membership,
        ).select_related(
            'membership',
            'membership__sacco',
            'transaction',
        )

        from_date = self.request.query_params.get('from_date')
        if from_date:
            queryset = queryset.filter(created_at__date__gte=from_date)

        to_date = self.request.query_params.get('to_date')
        if to_date:
            queryset = queryset.filter(created_at__date__lte=to_date)

        category = self.request.query_params.get('category')
        if category:
            queryset = queryset.filter(category=category)

        return queryset.order_by('-created_at')

    def _get_membership(self):
        sacco_id = self.request.query_params.get('sacco_id')
        if not sacco_id:
            raise ValidationError({'sacco_id': 'This query param is required.'})

        try:
            return Membership.objects.select_related('sacco').get(
                user=self.request.user,
                sacco_id=sacco_id,
                status=Membership.Status.APPROVED,
            )
        except Membership.DoesNotExist as exc:
            raise ValidationError(
                {'sacco_id': 'No approved membership found for this SACCO.'}
            ) from exc


class BalanceView(APIView):
    """Return the user's current ledger balance in a SACCO."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        membership = self._get_membership(request)
        data = {
            'sacco_id': membership.sacco_id,
            'sacco_name': membership.sacco.name,
            'current_balance': get_running_balance(membership),
            'as_of_date': None,
        }
        serializer = BalanceSerializer(data)
        return Response(serializer.data)

    def _get_membership(self, request):
        sacco_id = request.query_params.get('sacco_id')
        if not sacco_id:
            raise ValidationError({'sacco_id': 'This query param is required.'})

        try:
            return Membership.objects.select_related('sacco').get(
                user=request.user,
                sacco_id=sacco_id,
                status=Membership.Status.APPROVED,
            )
        except Membership.DoesNotExist as exc:
            raise ValidationError(
                {'sacco_id': 'No approved membership found for this SACCO.'}
            ) from exc


# ============================================================
# REVIEW - READ THIS THEN DELETE FROM THIS LINE TO THE END
# ============================================================
#
# What each class or function does and why:
# - get_running_balance reads LedgerEntry records for one membership, totals
#   CREDIT entries, totals DEBIT entries, and returns credits minus debits. This
#   makes the ledger the source of truth instead of Saving.amount.
# - get_balance_at_date is a small wrapper for asking the same balance question
#   at a specific date.
# - generate_reference creates a unique ledger reference such as
#   SAV-20260513103022-ABC123.
# - create_ledger_entry calculates balance_before, applies the credit or debit,
#   saves balance_after, and returns the LedgerEntry. It catches errors so a
#   payment callback does not crash because of a ledger write problem.
# - LedgerEntryListView returns paginated ledger entries for the logged-in
#   user's approved membership in the requested SACCO.
# - BalanceView returns the current ledger balance for the logged-in user's
#   approved membership in the requested SACCO.
# - process_stk_callback_task now uses create_ledger_entry for successful
#   saving deposits, so M-Pesa deposits are recorded in the ledger consistently.
#
# Django/Python concepts you might not know well:
# - aggregate(Sum('amount')) asks the database to calculate totals instead of
#   loading every row into Python.
# - Decimal is used for money so balances do not suffer from floating point
#   rounding errors.
# - A running balance is the balance after applying all ledger entries in time
#   order. balance_after stores that value at the moment an entry is created.
# - ListAPIView gives you filtering, serialization, and pagination behavior for
#   list endpoints.
# - ValidationError returns a clean 400 response when required query parameters
#   like sacco_id are missing.
#
# One manual test:
# - Log in as a member with an approved SACCO membership, complete an M-Pesa
#   saving deposit callback, then call GET /api/v1/ledger/balance/?sacco_id=<id>
#   and confirm the balance matches the CREDIT ledger entry amount.
#
# Important design decision:
# - The ledger is treated as the source of truth for balances. Saving.amount can
#   still be updated for quick product display, but financial balance endpoints
#   calculate from LedgerEntry records.
# - create_ledger_entry is the intended single entry point for writing ledger
#   rows because it centralizes reference generation and balance_after logic.
#
# END OF REVIEW - DELETE EVERYTHING FROM THE FIRST # LINE ABOVE
# ============================================================
