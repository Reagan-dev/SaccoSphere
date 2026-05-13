from decimal import Decimal, InvalidOperation

from django.core.cache import cache
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework.exceptions import ValidationError
from rest_framework.generics import ListAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .engines.activity_feed import get_activity_feed
from .engines.loan_comparator import compare_loan_options
from .engines.portfolio_builder import (
    get_dashboard_state,
    get_sacco_switcher_data,
    get_unified_portfolio,
)


class PortfolioView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_description='Get unified member portfolio across SACCO memberships.',
        responses={200: openapi.Response('OK'), 400: 'Bad Request', 401: 'Unauthorized'},
        security=[{'Bearer': []}],
    )
    def get(self, request):
        cache_key = f'portfolio:{request.user.id}'
        portfolio = cache.get(cache_key)
        if portfolio is not None:
            response = Response(portfolio, status=200)
            response['X-Cache'] = 'HIT'
            return response

        portfolio = get_unified_portfolio(request.user)
        cache.set(cache_key, portfolio, timeout=120)
        return Response(portfolio, status=200)


class DashboardStateView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_description='Get current dashboard state for authenticated member.',
        responses={200: openapi.Response('OK'), 400: 'Bad Request', 401: 'Unauthorized'},
        security=[{'Bearer': []}],
    )
    def get(self, request):
        cache_key = f'dashboard_state:{request.user.id}'
        state = cache.get(cache_key)
        if state is not None:
            response = Response(state, status=200)
            response['X-Cache'] = 'HIT'
            return response

        state = get_dashboard_state(request.user)
        cache.set(cache_key, state, timeout=60)
        return Response(state, status=200)


class SACCOSwitcherView(ListAPIView):
    permission_classes = [IsAuthenticated]
    pagination_class = None

    @swagger_auto_schema(
        operation_description='List SACCO switcher cards for authenticated member.',
        responses={200: openapi.Response('OK'), 400: 'Bad Request', 401: 'Unauthorized'},
        security=[{'Bearer': []}],
    )
    def get(self, request):
        return Response(get_sacco_switcher_data(request.user), status=200)


class ActivityFeedView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_description='Get member activity feed entries.',
        responses={200: openapi.Response('OK'), 400: 'Bad Request', 401: 'Unauthorized'},
        security=[{'Bearer': []}],
    )
    def get(self, request):
        limit = self._get_limit(request)
        cache_key = f'activity_feed:{request.user.id}:{limit}'
        activity = cache.get(cache_key)
        if activity is not None:
            response = Response(activity, status=200)
            response['X-Cache'] = 'HIT'
            return response

        activity = get_activity_feed(request.user, limit=limit)
        cache.set(cache_key, activity, timeout=60)
        return Response(activity, status=200)

    def _get_limit(self, request):
        raw_limit = request.query_params.get('limit', 20)

        try:
            limit = int(raw_limit)
        except (TypeError, ValueError) as exc:
            raise ValidationError({'limit': 'Limit must be a number.'}) from exc

        if limit < 1:
            raise ValidationError({'limit': 'Limit must be at least 1.'})

        return min(limit, 100)


class LoanComparisonView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_description='Compare eligible loan options for requested amount and term.',
        responses={200: openapi.Response('OK'), 400: 'Bad Request', 401: 'Unauthorized'},
        security=[{'Bearer': []}],
    )
    def get(self, request):
        amount = request.query_params.get('amount')
        term = request.query_params.get('term')

        if amount is None:
            raise ValidationError({'amount': 'Amount is required.'})
        if term is None:
            raise ValidationError({'term': 'Term is required.'})

        options = compare_loan_options(
            request.user,
            self._get_requested_amount(amount),
            self._get_term_months(term),
        )

        return Response(options, status=200)

    def _get_requested_amount(self, amount):
        try:
            requested_amount = Decimal(str(amount))
        except (InvalidOperation, TypeError) as exc:
            raise ValidationError(
                {'amount': 'Amount must be a valid number.'}
            ) from exc

        if requested_amount <= 0:
            raise ValidationError({'amount': 'Amount must be greater than 0.'})

        return requested_amount

    def _get_term_months(self, term):
        try:
            term_months = int(term)
        except (TypeError, ValueError) as exc:
            raise ValidationError({'term': 'Term must be a number.'}) from exc

        if term_months <= 0:
            raise ValidationError({'term': 'Term must be greater than 0.'})

        return term_months


# ============================================================
# REVIEW - READ THIS THEN DELETE FROM THIS LINE TO THE END
# ============================================================
#
# What each class or function does and why:
# - get_unified_portfolio builds one member-facing portfolio across all
#   approved SACCO memberships. It totals savings, loans, share capital, SACCO
#   summaries, and the latest 10 payment transactions.
# - get_per_sacco_summary turns one approved membership into a dashboard card
#   summary with savings buckets, active loan count, outstanding loan balance,
#   and the next unpaid instalment due date.
# - get_dashboard_state decides what dashboard state the frontend should show,
#   such as no SACCOs, pending memberships, active memberships, or suspended.
# - get_sacco_switcher_data builds the SACCO switcher list. It only includes
#   approved memberships, adds active savings and active loan counts, and keeps
#   unread SACCO notification counts ready for the UI badge.
# - get_activity_feed combines completed payments and paid loan repayments into
#   one newest-first list so the dashboard can show one activity timeline.
# - compare_loan_options checks each active loan type from the member's
#   approved SACCOs, skips products that do not fit the requested amount or
#   term, then calculates the monthly payment and total cost.
# - PortfolioView exposes the portfolio at /api/v1/dashboard/portfolio/.
# - DashboardStateView exposes the lightweight state at
#   /api/v1/dashboard/state/.
# - SACCOSwitcherView exposes the switcher at /api/v1/dashboard/saccos/.
# - ActivityFeedView exposes /api/v1/dashboard/activity/?limit=20 and caches it
#   for 60 seconds because activity can be shown often on dashboard refresh.
# - LoanComparisonView exposes
#   /api/v1/dashboard/loans/compare/?amount=&term= for loan shopping.
#
# Django/Python concepts that may be useful:
# - Prefetch loads related rows in bulk and stores them on each object. That is
#   why get_per_sacco_summary can read savings and loans without new queries.
# - select_related follows single foreign keys in the same SQL query.
# - annotate adds calculated fields to each row returned by a queryset. Here it
#   adds savings totals, loan counts, and notification counts to memberships.
# - Subquery lets one query include a small nested query, which is how unread
#   notification counts can be attached to each SACCO switcher row.
# - Django cache stores expensive dashboard results briefly so repeated page
#   loads do not rebuild the same portfolio every time.
# - X-Cache: HIT means the response came from cache instead of fresh database
#   aggregation.
# - Decimal is used for money so Python does not use floating point arithmetic
#   for currency values.
#
# One manual test:
# - Log in as a member with an approved SACCO membership, one completed payment,
#   and one paid loan instalment. Call GET /api/v1/dashboard/saccos/,
#   GET /api/v1/dashboard/activity/?limit=20, and
#   GET /api/v1/dashboard/loans/compare/?amount=50000&term=12. Confirm the
#   switcher only shows approved SACCOs, the activity feed is newest first, and
#   loan options are sorted by cheapest monthly payment.
#
# Important design decision:
# - The portfolio endpoint only includes APPROVED memberships. Pending,
#   suspended, rejected, or left memberships affect dashboard state but do not
#   count as active portfolio SACCOs.
# - The activity feed uses two database queries: one for payments and one for
#   repayments. The merge and sorting happen in Python because they come from
#   different database tables.
# - Payment transactions do not have a direct SACCO field, so the feed infers
#   SACCO name through the related M-Pesa saving or loan when that link exists.
# - Loan comparison is read-only. It does not create an application; it only
#   helps the member compare possible loan products before applying.
#
# END OF REVIEW - DELETE EVERYTHING FROM THE FIRST # LINE ABOVE
# ============================================================
