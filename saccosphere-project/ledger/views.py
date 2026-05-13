from math import ceil

from django.core.cache import cache
from django.http import HttpResponse
from django.utils.dateparse import parse_date
from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.exceptions import ValidationError
from rest_framework.generics import ListAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from config.pagination import FinancialPagination
from saccomembership.models import Membership

from .engines.balance_calculator import get_running_balance
from .engines.pdf_generator import generate_statement_pdf
from .engines.statement_builder import build_statement
from .models import LedgerEntry
from .serializers import (
    BalanceSerializer,
    LedgerEntrySerializer,
    StatementSerializer,
)


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


class StatementView(APIView):
    """Return a paginated ledger statement for a SACCO membership."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        from_date, to_date = self._get_date_range(request)
        membership = self._get_membership(request)
        cache_key = f'statement:{membership.id}:{from_date}:{to_date}'
        statement = cache.get(cache_key)

        if statement is None:
            statement = build_statement(
                membership,
                from_date,
                to_date,
                requesting_user=request.user,
            )
            cache.set(cache_key, statement, timeout=300)

        statement = statement.copy()
        statement['entries'], pagination = self._paginate_entries(
            statement['entries'],
            request,
        )
        serializer = StatementSerializer(statement)
        data = serializer.data
        data['entries_pagination'] = pagination

        return Response(data)

    def _get_date_range(self, request):
        from_date = self._parse_required_date(request, 'from_date')
        to_date = self._parse_required_date(request, 'to_date')

        if from_date > to_date:
            raise ValidationError(
                {'to_date': 'to_date must be on or after from_date.'}
            )

        if (to_date - from_date).days > 365:
            raise ValidationError(
                {'to_date': 'Statement date range cannot exceed 1 year.'}
            )

        return from_date, to_date

    def _parse_required_date(self, request, name):
        raw_value = request.query_params.get(name)
        if not raw_value:
            raise ValidationError({name: 'This query param is required.'})

        value = parse_date(raw_value)
        if value is None:
            raise ValidationError(
                {name: 'Use YYYY-MM-DD date format.'}
            )

        return value

    def _get_membership(self, request):
        sacco_id = request.query_params.get('sacco_id')
        if not sacco_id:
            raise ValidationError({'sacco_id': 'This query param is required.'})

        try:
            return Membership.objects.select_related('user', 'sacco').get(
                user=request.user,
                sacco_id=sacco_id,
                status=Membership.Status.APPROVED,
            )
        except Membership.DoesNotExist as exc:
            raise NotFound('No approved membership found for this SACCO.') from exc

    def _paginate_entries(self, entries, request):
        paginator = FinancialPagination()
        page = paginator.paginate_queryset(entries, request, view=self)

        if page is None:
            return entries, {
                'count': len(entries),
                'total_pages': 1,
                'current_page': 1,
                'next': None,
                'previous': None,
            }

        page_size = paginator.get_page_size(request)
        count = paginator.page.paginator.count
        return page, {
            'count': count,
            'total_pages': ceil(count / page_size) if page_size else 1,
            'current_page': paginator.page.number,
            'next': paginator.get_next_link(),
            'previous': paginator.get_previous_link(),
        }


class StatementPDFView(StatementView):
    """Return a PDF ledger statement for a SACCO membership."""

    def get(self, request):
        from_date, to_date = self._get_date_range(request)
        membership = self._get_membership(request)
        statement = build_statement(
            membership,
            from_date,
            to_date,
            requesting_user=request.user,
        )

        try:
            pdf_bytes = generate_statement_pdf(statement)
        except (ImportError, OSError):
            return Response(
                {'message': 'PDF generation temporarily unavailable.'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        filename = (
            f'statement_{membership.member_number or membership.id}_'
            f'{from_date}_{to_date}.pdf'
        )
        response = HttpResponse(pdf_bytes, content_type='application/pdf')
        response['Content-Disposition'] = (
            f'attachment; filename="{filename}"'
        )
        return response


# ============================================================
# REVIEW — READ THIS THEN DELETE FROM THIS LINE TO THE END
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
# - build_statement creates a date-range financial statement. It calculates
#   opening balance, closing balance, credit totals, debit totals, and serializes
#   the matching ledger entries.
# - StatementView exposes the statement at /api/v1/ledger/statement/. It checks
#   date inputs, limits statements to 1 year, caches the full statement for 5
#   minutes, and paginates entries in the response.
# - generate_statement_pdf renders the statement HTML template and asks
#   WeasyPrint to turn that HTML into PDF bytes.
# - StatementPDFView exposes /api/v1/ledger/statement/pdf/. It uses the same
#   validation as StatementView, builds the statement, generates a PDF, and
#   returns it as a downloadable file.
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
# - NotFound returns a clean 404 response when the user does not have an
#   approved membership in the requested SACCO.
# - Cache stores the generated statement briefly so repeated downloads or page
#   changes do not recalculate the same totals.
# - HttpResponse is used for PDFs because the response body is raw bytes, not
#   JSON data.
# - WeasyPrint converts HTML and CSS into a PDF. It needs native system
#   libraries on the server, not just the Python package.
#
# One manual test:
# - Log in as a member with an approved SACCO membership, complete an M-Pesa
#   saving deposit callback, then call GET /api/v1/ledger/balance/?sacco_id=<id>
#   and confirm the balance matches the CREDIT ledger entry amount.
# - Then call GET /api/v1/ledger/statement/?sacco_id=<id>&from_date=2026-01-01
#   &to_date=2026-12-31 and confirm opening balance, closing balance, totals,
#   and entries match the ledger records.
# - Call GET /api/v1/ledger/statement/pdf/?sacco_id=<id>&from_date=2026-01-01
#   &to_date=2026-12-31 and confirm the browser downloads a PDF statement.
#
# Important design decision:
# - The ledger is treated as the source of truth for balances. Saving.amount can
#   still be updated for quick product display, but financial balance endpoints
#   calculate from LedgerEntry records.
# - create_ledger_entry is the intended single entry point for writing ledger
#   rows because it centralizes reference generation and balance_after logic.
# - The statement builder returns a plain dictionary instead of a PDF. That
#   keeps JSON and future PDF generation using the same source data.
# - The PDF template uses inline CSS because WeasyPrint is most reliable when
#   it can render a self-contained HTML document without JavaScript or external
#   frontend assets.
#
# END OF REVIEW — DELETE EVERYTHING FROM THE FIRST # LINE ABOVE
# ============================================================
