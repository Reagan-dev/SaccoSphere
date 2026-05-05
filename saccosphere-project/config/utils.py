from uuid import uuid4


def get_request_id(request):
    return request.headers.get('X-Correlation-ID') or str(uuid4())
