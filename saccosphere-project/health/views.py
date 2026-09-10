from django.core.cache import cache
from django.db import connection
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import JobHeartbeat
from .monitored_jobs import MONITORED_JOBS


class LivenessView(APIView):
    authentication_classes = []
    permission_classes = []

    def get(self, request):
        return Response({'status': 'ok'})


class ReadinessView(APIView):
    authentication_classes = []
    permission_classes = []

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

    def get(self, request):
        now = timezone.now()
        heartbeats = {
            hb.job_name: hb
            for hb in JobHeartbeat.objects.filter(
                job_name__in=MONITORED_JOBS.keys(),
            )
        }

        jobs = {}
        all_healthy = True
        for job_name, max_age in MONITORED_JOBS.items():
            hb = heartbeats.get(job_name)
            if hb is None:
                jobs[job_name] = {
                    'status': 'missing',
                    'last_run_at': None,
                    'age_seconds': None,
                    'max_age_seconds': int(max_age.total_seconds()),
                }
                all_healthy = False
                continue

            age = now - hb.last_run_at
            is_stale = age > max_age
            is_errored = hb.last_status == JobHeartbeat.Status.ERROR
            healthy = not is_stale and not is_errored
            all_healthy = all_healthy and healthy

            if is_errored:
                job_status = 'errored'
            elif is_stale:
                job_status = 'stale'
            else:
                job_status = 'ok'

            jobs[job_name] = {
                'status': job_status,
                'last_run_at': hb.last_run_at.isoformat(),
                'last_status': hb.last_status,
                'age_seconds': int(age.total_seconds()),
                'max_age_seconds': int(max_age.total_seconds()),
            }

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
