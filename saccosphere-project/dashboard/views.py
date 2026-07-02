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


