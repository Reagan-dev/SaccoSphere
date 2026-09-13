from django.core.cache import cache
from django.db import connection
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .services import evaluate_job_health


class LivenessView(APIView):
    authentication_classes = []
    permission_classes = []
    throttle_classes = []

    def get(self, request):
        return Response({'status': 'ok'})


class ReadinessView(APIView):
    authentication_classes = []
    permission_classes = []
    throttle_classes = []

    def get(self, request):
        checks = {
            'database': self._database_ready(),
            'cache': self._cache_ready(),
        }
        ready = all(checks.values())
        response_status = (
            status.HTTP_200_OK
            if ready
            else status.HTTP_503_SERVICE_UNAVAILABLE
        )

        return Response(
            {
                'status': 'ok' if ready else 'unavailable',
                'checks': checks,
            },
            status=response_status,
        )

    def _database_ready(self):
        try:
            with connection.cursor() as cursor:
                cursor.execute('SELECT 1')
                cursor.fetchone()
        except Exception:
            return False

        return True

    def _cache_ready(self):
        try:
            cache.set('health:readiness', 'ok', timeout=5)
            return cache.get('health:readiness') == 'ok'
        except Exception:
            return False


class JobHealthView(APIView):
    """Report freshness of the scheduled background jobs in MONITORED_JOBS.

    200 when every monitored job has run within its allowed age and its
    last run was OK; 503 when any is stale, missing, or last errored -
    the signal an external monitor alerts on. Deliberately separate from
    ``/health/ready/``: a lagging batch job must not pull the web tier
    out of the load balancer.
    """

    authentication_classes = []
    permission_classes = []
    throttle_classes = []

    def get(self, request):
        all_healthy, jobs = evaluate_job_health()

        response_status = (
            status.HTTP_200_OK
            if all_healthy
            else status.HTTP_503_SERVICE_UNAVAILABLE
        )
        return Response(
            {
                'status': 'ok' if all_healthy else 'degraded',
                'jobs': jobs,
            },
            status=response_status,
        )


HealthCheckView = LivenessView
ReadinessCheckView = ReadinessView
