from rest_framework.views import APIView

from config.response import StandardResponseMixin


class HealthCheckView(StandardResponseMixin, APIView):
    authentication_classes = []
    permission_classes = []

    def get(self, request):
        return self.ok({'status': 'ok'}, 'SaccoSphere is healthy')

    def post(self, request):
        return self.created(
            {
                'status': 'ok',
                'received': request.data,
            },
            'Health check post received',
        )


class ReadinessCheckView(StandardResponseMixin, APIView):
    authentication_classes = []
    permission_classes = []

    def get(self, request):
        return self.ok({'ready': True}, 'SaccoSphere is ready')
