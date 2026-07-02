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


