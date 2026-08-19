import logging
import hmac

from django.conf import settings
from django.db import transaction
from rest_framework import status
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from .models import KYCVerification, User
from .serializers import GoogleAuthSerializer, UserProfileSerializer
from .throttles import GoogleOAuthThrottle

logger = logging.getLogger('saccosphere.oauth')

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

    audience = _get_google_allowed_client_ids()
    try:
        return id_token.verify_oauth2_token(
            raw_id_token,
            requests.Request(),
            audience,
        )
    except ValueError as exc:
        raise AuthenticationFailed('Invalid Google token.') from exc


def _get_google_allowed_client_ids():
    """Read the list of allowed Google client IDs from settings."""
    client_ids = getattr(settings, 'GOOGLE_OAUTH_ALLOWED_CLIENT_IDS', [])
    if client_ids:
        return client_ids

    # Fallback to legacy single client ID for backward compatibility
    client_id = getattr(settings, 'GOOGLE_OAUTH_CLIENT_ID', '')
    if client_id:
        return [client_id]

    client_id = getattr(settings, 'GOOGLE_CLIENT_ID', '')
    if client_id:
        return [client_id]

    providers = getattr(settings, 'SOCIALACCOUNT_PROVIDERS', {})
    google_settings = providers.get('google', {})
    app_settings = google_settings.get('APP', {})
    legacy_client_id = app_settings.get('client_id')
    if legacy_client_id:
        return [legacy_client_id]

    return []


class GoogleOAuthCallbackView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [GoogleOAuthThrottle]

    def post(self, request):
        serializer = GoogleAuthSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        flow = serializer.validated_data['flow']
        token_payload = verify_google_id_token(
            serializer.validated_data['id_token'],
        )
        
        # Validate nonce for replay protection
        self._validate_nonce(
            serializer.validated_data.get('nonce'),
            token_payload,
        )
        
        email = token_payload.get('email')
        email_verified = token_payload.get('email_verified', False)
        google_sub = token_payload.get('sub')

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
            # Check if user has linked Google account
            if not user.google_id:
                # User has password-only account
                if not email_verified:
                    return Response(
                        {'error': 'Google account email is not verified. '
                                 'Please verify your email with Google.'},
                        status=status.HTTP_401_UNAUTHORIZED,
                    )
                # Return 409 for password-only account
                return Response(
                    {
                        'code': 'account_exists_password_only',
                        'detail': 'An account already exists with this email. '
                                  'Log in with your password, then connect Google '
                                  'from your profile settings.',
                    },
                    status=status.HTTP_409_CONFLICT,
                )
            # User has linked Google account, proceed with login
            return Response(self._build_token_payload(user), status=200)

        if user is not None:
            # Signup flow with existing user
            if user.google_id:
                # User already has Google linked, log them in
                payload = self._build_token_payload(user)
                payload['is_existing_user'] = True
                payload['message'] = (
                    'Account already exists — you have been logged in.'
                )
                return Response(payload, status=status.HTTP_200_OK)
            # User has password-only account
            if not email_verified:
                return Response(
                    {'error': 'Google account email is not verified. '
                             'Please verify your email with Google.'},
                    status=status.HTTP_401_UNAUTHORIZED,
                )
            # Return 409 for password-only account
            return Response(
                {
                    'code': 'account_exists_password_only',
                    'detail': 'An account already exists with this email. '
                              'Log in with your password, then connect Google '
                              'from your profile settings.',
                },
                status=status.HTTP_409_CONFLICT,
            )

        user = self._create_user_from_google(token_payload, google_sub)
        payload = self._build_token_payload(user)
        payload['is_existing_user'] = False
        return Response(payload, status=status.HTTP_201_CREATED)

    def _create_user_from_google(self, token_payload, google_sub=None):
        email = token_payload['email']
        first_name, last_name = self._get_names(token_payload)

        with transaction.atomic():
            user = User.objects.create_user(
                email=email,
                password=None,
                first_name=first_name,
                last_name=last_name,
                google_id=google_sub,
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

    def _validate_nonce(self, request_nonce, token_payload):
        """
        Validate nonce for replay protection.
        
        The mobile app generates a nonce, passes it to Google's SDK when
        requesting the token, and sends it alongside the ID token. This
        method verifies that the nonce in the token matches the one sent
        in the request using constant-time comparison to prevent timing
        attacks.
        
        If no nonce is provided and NONCE_REQUIRED is False, a warning is
        logged but the request proceeds (for backward compatibility with
        older mobile app versions). If NONCE_REQUIRED is True, missing
        nonces are rejected with 401.
        """
        token_nonce = token_payload.get('nonce')
        
        if request_nonce:
            # Use constant-time comparison to prevent timing attacks
            if not hmac.compare_digest(
                request_nonce.encode('utf-8'),
                (token_nonce or '').encode('utf-8'),
            ):
                logger.warning(
                    'Nonce mismatch in Google OAuth callback: '
                    'request nonce does not match token nonce.'
                )
                raise AuthenticationFailed(
                    'Nonce validation failed. The provided nonce does not '
                    'match the token\'s nonce claim.'
                )
        else:
            # No nonce provided in request
            if getattr(settings, 'NONCE_REQUIRED', False):
                raise AuthenticationFailed(
                    'Nonce is required for Google OAuth but was not provided.'
                )
            # Log warning for monitoring - helps identify outdated app versions
            logger.warning(
                'Google OAuth callback received without nonce protection. '
                'This may indicate an outdated mobile app version.'
            )


class GoogleOAuthLinkView(APIView):
    """
    Link a Google account to an authenticated user's existing account.
    
    Requires the user to be authenticated with their existing credentials
    (password or another method). Verifies the Google ID token and links
    the Google identity to the current user account.
    """
    permission_classes = [IsAuthenticated]
    throttle_classes = [GoogleOAuthThrottle]

    def post(self, request):
        serializer = GoogleAuthSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        token_payload = verify_google_id_token(
            serializer.validated_data['id_token'],
        )
        
        # Validate nonce for replay protection
        self._validate_nonce(
            serializer.validated_data.get('nonce'),
            token_payload,
        )
        
        email = token_payload.get('email')
        email_verified = token_payload.get('email_verified', False)
        google_sub = token_payload.get('sub')

        if not email:
            return Response(
                {'error': 'Google account email is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not email_verified:
            return Response(
                {'error': 'Google account email is not verified. '
                         'Please verify your email with Google.'},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        # Check if Google email matches current user's email (case-insensitive)
        if email.lower() != request.user.email.lower():
            return Response(
                {'error': 'Google account email does not match your account email.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Check if Google identity is already linked to another account
        existing_google_user = User.objects.filter(google_id=google_sub).first()
        if existing_google_user and existing_google_user != request.user:
            return Response(
                {
                    'code': 'google_identity_already_linked',
                    'detail': 'This Google account is already linked to another account.',
                },
                status=status.HTTP_409_CONFLICT,
            )

        # Link Google account to current user
        request.user.google_id = google_sub
        request.user.save()

        return Response(
            {'message': 'Google account linked successfully.'},
            status=status.HTTP_200_OK,
        )

    def _validate_nonce(self, request_nonce, token_payload):
        """
        Validate nonce for replay protection.
        
        The mobile app generates a nonce, passes it to Google's SDK when
        requesting the token, and sends it alongside the ID token. This
        method verifies that the nonce in the token matches the one sent
        in the request using constant-time comparison to prevent timing
        attacks.
        
        If no nonce is provided and NONCE_REQUIRED is False, a warning is
        logged but the request proceeds (for backward compatibility with
        older mobile app versions). If NONCE_REQUIRED is True, missing
        nonces are rejected with 401.
        """
        token_nonce = token_payload.get('nonce')
        
        if request_nonce:
            # Use constant-time comparison to prevent timing attacks
            if not hmac.compare_digest(
                request_nonce.encode('utf-8'),
                (token_nonce or '').encode('utf-8'),
            ):
                logger.warning(
                    'Nonce mismatch in Google OAuth link: '
                    'request nonce does not match token nonce.'
                )
                raise AuthenticationFailed(
                    'Nonce validation failed. The provided nonce does not '
                    'match the token\'s nonce claim.'
                )
        else:
            # No nonce provided in request
            if getattr(settings, 'NONCE_REQUIRED', False):
                raise AuthenticationFailed(
                    'Nonce is required for Google OAuth but was not provided.'
                )
            # Log warning for monitoring - helps identify outdated app versions
            logger.warning(
                'Google OAuth link received without nonce protection. '
                'This may indicate an outdated mobile app version.'
            )
