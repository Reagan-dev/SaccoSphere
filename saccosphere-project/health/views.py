from django.core.cache import cache
from django.db import connection
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView


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


HealthCheckView = LivenessView
ReadinessCheckView = ReadinessView
