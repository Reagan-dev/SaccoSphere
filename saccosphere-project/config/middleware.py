from asgiref.local import Local
from django.utils.deprecation import MiddlewareMixin

from .utils import get_request_id


_request_context = Local()


def get_current_sacco_id():
    return getattr(_request_context, 'sacco_id', None)


def get_current_correlation_id():
    return getattr(_request_context, 'correlation_id', None)


class CorrelationIdMiddleware(MiddlewareMixin):
    def process_request(self, request):
        correlation_id = get_request_id(request)
        request.correlation_id = correlation_id
        _request_context.correlation_id = correlation_id

    def process_response(self, request, response):
        response['X-Correlation-ID'] = getattr(
            request,
            'correlation_id',
            get_request_id(request),
        )
        return response


class SaccoContextMiddleware(MiddlewareMixin):
    def process_request(self, request):
        sacco_id = request.headers.get('X-SACCO-ID')
        request.sacco_id = sacco_id
        _request_context.sacco_id = sacco_id

    def process_response(self, request, response):
        _request_context.sacco_id = None
        return response
