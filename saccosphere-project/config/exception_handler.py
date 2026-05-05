from rest_framework.views import exception_handler


def custom_exception_handler(exc, context):
    response = exception_handler(exc, context)

    if response is None:
        return response

    request = context.get('request')
    response.data = {
        'success': False,
        'status_code': response.status_code,
        'error': response.data,
        'correlation_id': getattr(request, 'correlation_id', None),
    }
    return response
