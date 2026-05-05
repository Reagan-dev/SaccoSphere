from rest_framework import status
from rest_framework.exceptions import NotAuthenticated
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response


def success_response(data=None, message='Success', status_code=status.HTTP_200_OK):
    return Response(
        {
            'success': True,
            'message': message,
            'data': data,
        },
        status=status_code,
    )


class StandardResponseMixin:
    def ok(self, data=None, message='Success'):
        return success_response(data, message, status.HTTP_200_OK)

    def created(self, data=None, message='Created successfully'):
        return success_response(data, message, status.HTTP_201_CREATED)

    def no_content(self, message='Deleted'):
        return Response(
            {
                'success': True,
                'message': message,
                'data': None,
            },
            status=status.HTTP_204_NO_CONTENT,
        )

    def bad_request(self, message, errors=None):
        return Response(
            {
                'success': False,
                'message': message,
                'errors': errors,
                'error_code': 'VALIDATION_ERROR',
                'status_code': status.HTTP_400_BAD_REQUEST,
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    def not_found(self, message='Not found'):
        return Response(
            {
                'success': False,
                'message': message,
                'errors': None,
                'error_code': 'NOT_FOUND',
                'status_code': status.HTTP_404_NOT_FOUND,
            },
            status=status.HTTP_404_NOT_FOUND,
        )

    def permission_denied(self, request=None, message=None, code=None):
        if request is not None and not isinstance(request, str):
            if request.authenticators and not request.successful_authenticator:
                raise NotAuthenticated()

            raise PermissionDenied(detail=message, code=code)

        message = message or request
        return Response(
            {
                'success': False,
                'message': message,
                'errors': None,
                'error_code': 'PERMISSION_DENIED',
                'status_code': status.HTTP_403_FORBIDDEN,
            },
            status=status.HTTP_403_FORBIDDEN,
        )

    def server_error(self, message):
        return Response(
            {
                'success': False,
                'message': message,
                'errors': None,
                'error_code': 'SERVER_ERROR',
                'status_code': status.HTTP_500_INTERNAL_SERVER_ERROR,
            },
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
