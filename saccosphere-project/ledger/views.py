import logging
from math import ceil
import uuid

from django.core.cache import cache
from django.http import HttpResponse
from django.utils.dateparse import parse_date
from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.exceptions import ValidationError
from rest_framework.generics import ListAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView

from config.pagination import FinancialPagination
from saccomembership.models import Membership

from .engines.balance_calculator import get_running_balance
from .engines.pdf_generator import generate_statement_pdf
from .engines.statement_builder import build_statement, record_statement_access
from .models import LedgerEntry
from .serializers import (
    BalanceSerializer,
    LedgerEntrySerializer,
    StatementSerializer,
)


logger = logging.getLogger('saccosphere.ledger')


class StatementPDFThrottle(UserRateThrottle):
    """Dedicated rate limit for the synchronous WeasyPrint PDF render.

    Kept separate from the blanket ``user`` scope (1000/hour) because
    rendering a statement to PDF is CPU-heavy and runs in the
    request/response cycle - a member downloading their own statement a
    few times an hour is normal; hundreds of times is not.
    """

    scope = 'ledger_statement_pdf'


def _safe_emit_metric(event, **tags):
    """Emit a metric without letting a metrics hiccup break the request."""
    try:
        from config.utils import emit_metric

        emit_metric(event, **tags)
    except Exception:
        logger.exception('Failed to emit metric %s.', event)


def _parse_uuid_param(request, name):
    """Parse a required UUID query param.

    Raises a DRF ``ValidationError`` (400) on a missing or malformed
    value, instead of letting a bad UUID reach the ORM and blow up as an
    unhandled ``django.core.exceptions.ValidationError`` (500).
    """
    raw_value = request.query_params.get(name)
    if not raw_value:
        raise ValidationError({name: 'This query param is required.'})

    try:
        return uuid.UUID(raw_value)
    except ValueError as exc:
        raise ValidationError({name: 'Must be a valid UUID.'}) from exc


def _parse_date_param(request, name, *, required):
    """Parse a query param as a date.

    Raises a DRF ``ValidationError`` (400) on a missing required value or
    a malformed one, instead of letting a bad date string reach the ORM
    and blow up as an unhandled ``django.core.exceptions.ValidationError``
    (500). Returns ``None`` for an absent optional param.
    """
    raw_value = request.query_params.get(name)
    if not raw_value:
        if required:
            raise ValidationError({name: 'This query param is required.'})
        return None

    value = parse_date(raw_value)
    if value is None:
        raise ValidationError({name: 'Use YYYY-MM-DD date format.'})

    return value


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

        from_date = _parse_date_param(
            self.request, 'from_date', required=False,
        )
        if from_date:
            queryset = queryset.filter(created_at__date__gte=from_date)

        to_date = _parse_date_param(self.request, 'to_date', required=False)
        if to_date:
            queryset = queryset.filter(created_at__date__lte=to_date)

        category = self.request.query_params.get('category')
        if category:
            queryset = queryset.filter(category=category)

        return queryset.order_by('-created_at')

    def _get_membership(self):
        sacco_id = _parse_uuid_param(self.request, 'sacco_id')

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
        sacco_id = _parse_uuid_param(request, 'sacco_id')

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
            _safe_emit_metric('ledger_statement_cache_miss')
            statement = build_statement(
                membership,
                from_date,
                to_date,
                requesting_user=request.user,
            )
            cache.set(cache_key, statement, timeout=300)
        else:
            _safe_emit_metric('ledger_statement_cache_hit')
            # build_statement() logs the ODPC access record itself, but a
            # cache hit skips build_statement() entirely - log the access
            # explicitly here so every view of a statement is recorded,
            # not just the first one within the cache window.
            record_statement_access(membership, request.user)

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
        from_date = _parse_date_param(request, 'from_date', required=True)
        to_date = _parse_date_param(request, 'to_date', required=True)

        if from_date > to_date:
            raise ValidationError(
                {'to_date': 'to_date must be on or after from_date.'}
            )

        if (to_date - from_date).days > 365:
            raise ValidationError(
                {'to_date': 'Statement date range cannot exceed 1 year.'}
            )

        return from_date, to_date

    def _get_membership(self, request):
        sacco_id = _parse_uuid_param(request, 'sacco_id')

        try:
            return Membership.objects.select_related('user', 'sacco').get(
                user=request.user,
                sacco_id=sacco_id,
                status=Membership.Status.APPROVED,
            )
        except Membership.DoesNotExist as exc:
            raise NotFound(
                'No approved membership found for this SACCO.'
            ) from exc

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

    throttle_classes = [StatementPDFThrottle]

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
        except (ImportError, OSError) as exc:
            logger.exception(
                'Statement PDF generation unavailable for membership %s.',
                membership.id,
            )
            _safe_emit_metric(
                'ledger_statement_pdf_failed',
                reason=type(exc).__name__,
            )
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
