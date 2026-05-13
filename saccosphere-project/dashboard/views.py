from django.core.cache import cache
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .engines.portfolio_builder import (
    get_dashboard_state,
    get_unified_portfolio,
)


class PortfolioView(APIView):
    permission_classes = [IsAuthenticated]

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


# ============================================================
# REVIEW — READ THIS THEN DELETE FROM THIS LINE TO THE END
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
# - PortfolioView exposes the portfolio at /api/v1/dashboard/portfolio/.
# - DashboardStateView exposes the lightweight state at
#   /api/v1/dashboard/state/.
#
# Django/Python concepts that may be useful:
# - Prefetch loads related rows in bulk and stores them on each object. That is
#   why get_per_sacco_summary can read savings and loans without new queries.
# - select_related follows single foreign keys in the same SQL query.
# - Django cache stores expensive dashboard results briefly so repeated page
#   loads do not rebuild the same portfolio every time.
# - X-Cache: HIT means the response came from cache instead of fresh database
#   aggregation.
#
# One manual test:
# - Log in as a member with an approved SACCO membership and active savings,
#   then call GET /api/v1/dashboard/portfolio/. Call it twice and confirm the
#   second response includes X-Cache: HIT.
#
# Important design decision:
# - The portfolio endpoint only includes APPROVED memberships. Pending,
#   suspended, rejected, or left memberships affect dashboard state but do not
#   count as active portfolio SACCOs.
#
# END OF REVIEW — DELETE EVERYTHING FROM THE FIRST # LINE ABOVE
# ============================================================
