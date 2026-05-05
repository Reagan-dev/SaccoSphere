import logging
import time

from asgiref.local import Local
from django.utils.deprecation import MiddlewareMixin

from .utils import get_request_id


_request_context = Local()
request_logger = logging.getLogger('saccosphere.requests')


def get_current_sacco_id():
    return getattr(_request_context, 'sacco_id', None)


def get_current_correlation_id():
    return getattr(_request_context, 'correlation_id', None)


class RequestCorrelationMiddleware(MiddlewareMixin):
    def process_request(self, request):
        correlation_id = get_request_id(request)
        request.correlation_id = correlation_id
        _request_context.correlation_id = correlation_id

    def process_response(self, request, response):
        correlation_id = getattr(request, 'correlation_id', get_request_id(request))
        response['X-Correlation-ID'] = correlation_id
        _request_context.correlation_id = None
        return response


class LoggingMiddleware(MiddlewareMixin):
    def process_request(self, request):
        request.start_time = time.monotonic()

    def process_response(self, request, response):
        response_time_ms = round(
            (time.monotonic() - getattr(request, 'start_time', time.monotonic()))
            * 1000,
            2,
        )
        user = getattr(request, 'user', None)
        username = user.get_username() if user and user.is_authenticated else 'anonymous'

        request_logger.info(
            'method=%s path=%s user=%s status_code=%s '
            'response_time_ms=%s correlation_id=%s',
            request.method,
            request.path,
            username,
            response.status_code,
            response_time_ms,
            getattr(request, 'correlation_id', '-'),
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


CorrelationIdMiddleware = RequestCorrelationMiddleware
