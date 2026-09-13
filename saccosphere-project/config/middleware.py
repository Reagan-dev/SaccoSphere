import logging
import time

from asgiref.local import Local
from django.utils.deprecation import MiddlewareMixin

from .utils import get_request_id


request_logger = logging.getLogger('saccosphere.requests')
_request_context = Local()


def get_current_sacco_id():
    return getattr(_request_context, 'sacco_id', None)


def set_current_sacco_id(sacco_id):
    _request_context.sacco_id = sacco_id


def get_current_correlation_id():
    return getattr(_request_context, 'correlation_id', None)


class RequestContextFilter(logging.Filter):
    """Attach the current request's correlation/SACCO ids to log records.

    Values come from the thread-locals populated by
    ``RequestCorrelationMiddleware`` and
    ``saccomanagement.middleware.SaccoContextMiddleware``. Outside of a
    request (management commands, Celery tasks), both default to '-'.
    """

    def filter(self, record):
        record.correlation_id = get_current_correlation_id() or '-'
        record.sacco_id = get_current_sacco_id() or '-'
        return True


class RequestCorrelationMiddleware(MiddlewareMixin):
    def process_request(self, request):
        correlation_id = get_request_id(request)
        request.correlation_id = correlation_id
        _request_context.correlation_id = correlation_id
        # Reset here, at the very start of every request: DRF resolves
        # the authenticated user (JWT) well after Django's own
        # middleware has run, so SaccoScopedMixin.initial() - not
        # SaccoContextMiddleware - is what sets the real value later
        # in the request/response cycle for API views. Resetting first
        # guarantees a thread reused across requests never logs a
        # sacco_id left over from a previous one.
        _request_context.sacco_id = None

    def process_response(self, request, response):
        correlation_id = getattr(
            request, 'correlation_id', get_request_id(request),
        )
        response['X-Correlation-ID'] = correlation_id
        _request_context.correlation_id = None
        return response


class LoggingMiddleware(MiddlewareMixin):
    def process_request(self, request):
        request.start_time = time.monotonic()

    def process_response(self, request, response):
        start_time = getattr(request, 'start_time', time.monotonic())
        response_time_ms = round(
            (time.monotonic() - start_time) * 1000,
            2,
        )
        user = getattr(request, 'user', None)
        if user and user.is_authenticated:
            username = user.get_username()
        else:
            username = 'anonymous'

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


CorrelationIdMiddleware = RequestCorrelationMiddleware
