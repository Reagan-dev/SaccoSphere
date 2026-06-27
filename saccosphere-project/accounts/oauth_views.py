from django.conf import settings
from django.db import transaction
from rest_framework import status
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from .models import KYCVerification, User
from .serializers import GoogleAuthSerializer, UserProfileSerializer


LOGIN_ACCOUNT_NOT_FOUND = (
    'No account found with this Google account. Please sign up first.'
)


def verify_google_id_token(raw_id_token):
    """Verify a Google id_token and return its decoded claims."""
    try:
        from google.auth.transport import requests
        from google.oauth2 import id_token
    except ImportError as exc:
        raise AuthenticationFailed(
            'Google token verification is not configured.'
        ) from exc

    audience = _get_google_client_id()
    try:
        return id_token.verify_oauth2_token(
            raw_id_token,
            requests.Request(),
            audience,
        )
    except ValueError as exc:
        raise AuthenticationFailed('Invalid Google token.') from exc


def _get_google_client_id():
    """Read the Google client id from common allauth/settings locations."""
    client_id = getattr(settings, 'GOOGLE_OAUTH_CLIENT_ID', '')
    if client_id:
        return client_id

    client_id = getattr(settings, 'GOOGLE_CLIENT_ID', '')
    if client_id:
        return client_id

    providers = getattr(settings, 'SOCIALACCOUNT_PROVIDERS', {})
    google_settings = providers.get('google', {})
    app_settings = google_settings.get('APP', {})
    return app_settings.get('client_id')


class GoogleOAuthCallbackView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = GoogleAuthSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        flow = serializer.validated_data['flow']
        token_payload = verify_google_id_token(
            serializer.validated_data['id_token'],
        )
        email = token_payload.get('email')

        if not email:
            return Response(
                {'error': 'Google account email is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user = User.objects.filter(email__iexact=email).first()
        if flow == 'login':
            if user is None:
                return Response(
                    {'error': LOGIN_ACCOUNT_NOT_FOUND},
                    status=status.HTTP_404_NOT_FOUND,
                )
            return Response(self._build_token_payload(user), status=200)

        if user is not None:
            payload = self._build_token_payload(user)
            payload['is_existing_user'] = True
            payload['message'] = (
                'Account already exists — you have been logged in.'
            )
            return Response(payload, status=status.HTTP_200_OK)

        user = self._create_user_from_google(token_payload)
        payload = self._build_token_payload(user)
        payload['is_existing_user'] = False
        return Response(payload, status=status.HTTP_201_CREATED)

    def _create_user_from_google(self, token_payload):
        email = token_payload['email']
        first_name, last_name = self._get_names(token_payload)

        with transaction.atomic():
            user = User.objects.create_user(
                email=email,
                password=None,
                first_name=first_name,
                last_name=last_name,
            )
            KYCVerification.objects.get_or_create(
                user=user,
                defaults={'status': KYCVerification.Status.NOT_STARTED},
            )
        return user

    def _get_names(self, token_payload):
        first_name = token_payload.get('given_name') or ''
        last_name = token_payload.get('family_name') or ''
        name = token_payload.get('name') or ''

        if first_name or last_name or not name:
            return first_name, last_name

        parts = name.split(maxsplit=1)
        if len(parts) == 1:
            return parts[0], ''
        return parts[0], parts[1]

    def _build_token_payload(self, user):
        refresh = RefreshToken.for_user(user)
        return {
            'access': str(refresh.access_token),
            'refresh': str(refresh),
            'user': UserProfileSerializer(user).data,
        }
