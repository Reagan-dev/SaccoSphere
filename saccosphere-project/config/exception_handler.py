from django.http import Http404
from rest_framework import status
from rest_framework.exceptions import (
    APIException,
    AuthenticationFailed,
    MethodNotAllowed,
    NotAuthenticated,
    NotFound,
    PermissionDenied,
    Throttled,
    ValidationError,
)
from rest_framework.response import Response
from rest_framework.views import exception_handler


ERROR_CODE_BY_STATUS = {
    status.HTTP_400_BAD_REQUEST: 'VALIDATION_ERROR',
    status.HTTP_401_UNAUTHORIZED: 'UNAUTHORIZED',
    status.HTTP_403_FORBIDDEN: 'PERMISSION_DENIED',
    status.HTTP_404_NOT_FOUND: 'NOT_FOUND',
    status.HTTP_405_METHOD_NOT_ALLOWED: 'METHOD_NOT_ALLOWED',
    status.HTTP_429_TOO_MANY_REQUESTS: 'RATE_LIMIT_EXCEEDED',
    status.HTTP_500_INTERNAL_SERVER_ERROR: 'SERVER_ERROR',
}


def _normalize_errors(detail):
    if detail is None or isinstance(detail, dict):
        return detail

    return {'detail': detail}


def _get_error_code(exc, status_code):
    if isinstance(exc, ValidationError):
        return 'VALIDATION_ERROR'
    if isinstance(exc, (AuthenticationFailed, NotAuthenticated)):
        return 'UNAUTHORIZED'
    if isinstance(exc, PermissionDenied):
        return 'PERMISSION_DENIED'
    if isinstance(exc, (Http404, NotFound)):
        return 'NOT_FOUND'
    if isinstance(exc, MethodNotAllowed):
        return 'METHOD_NOT_ALLOWED'
    if isinstance(exc, Throttled):
        return 'RATE_LIMIT_EXCEEDED'

    return ERROR_CODE_BY_STATUS.get(status_code, 'SERVER_ERROR')


def _get_message(exc, status_code, errors):
    if status_code >= status.HTTP_500_INTERNAL_SERVER_ERROR:
        return 'Internal server error'

    if isinstance(exc, ValidationError):
        return 'Validation error'

    if isinstance(errors, dict):
        detail = errors.get('detail')
        if detail:
            return str(detail)

    if isinstance(exc, APIException) and exc.detail:
        return str(exc.detail)

    return 'Request failed'


def custom_exception_handler(exc, context):
    response = exception_handler(exc, context)

    if response is None:
        status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
        errors = None
    else:
        status_code = response.status_code
        errors = _normalize_errors(response.data)

    response_data = {
        'success': False,
        'message': _get_message(exc, status_code, errors),
        'errors': errors,
        'error_code': _get_error_code(exc, status_code),
        'status_code': status_code,
    }

    return Response(response_data, status=status_code)
