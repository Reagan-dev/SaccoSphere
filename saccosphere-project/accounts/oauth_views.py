import logging
import hmac

from django.conf import settings
from django.core.cache import cache
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

logger = logging.getLogger('accounts.oauth')

# Nonce TTL: slightly longer than Google ID token lifetime (1 hour)
# to account for clock skew and network delays
NONCE_TTL_SECONDS = 3900  # 65 minutes


def _mask_email(email):
    """
    Mask an email address for logging.
    
    Keeps the first character of the local part, masks the rest with asterisks,
    and keeps the full domain. Example: 'john@example.com' -> 'j***@example.com'.
    """
    if not email:
        return 'unknown'
    try:
        local, domain = email.rsplit('@', 1)
        if len(local) <= 1:
            masked_local = local
        else:
            masked_local = local[0] + '*' * (len(local) - 1)
        return f'{masked_local}@{domain}'
    except ValueError:
        return 'invalid'


def _get_client_ip(request):
    """
    Get the client IP address from the request.
    
    Checks for X-Forwarded-For header first (for reverse proxies),
    then falls back to REMOTE_ADDR.
    """
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        # X-Forwarded-For can contain multiple IPs, take the first one
        ip = x_forwarded_for.split(',')[0].strip()
    else:
        ip = request.META.get('REMOTE_ADDR', 'unknown')
    return ip

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
        ip_address = _get_client_ip(request)
        serializer = GoogleAuthSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        flow = serializer.validated_data['flow']
        
        try:
            token_payload = verify_google_id_token(
                serializer.validated_data['id_token'],
            )
        except AuthenticationFailed as exc:
            logger.warning(
                'Google OAuth failed: token verification failed',
                extra={
                    'outcome': 'token_verification_failed',
                    'ip_address': ip_address,
                    'flow': flow,
                }
            )
            raise
        
        # Validate nonce for replay protection
        try:
            self._validate_nonce(
                serializer.validated_data.get('nonce'),
                token_payload,
            )
        except AuthenticationFailed as exc:
            logger.warning(
                'Google OAuth failed: nonce validation failed',
                extra={
                    'outcome': 'nonce_validation_failed',
                    'ip_address': ip_address,
                    'flow': flow,
                }
            )
            raise
        
        email = token_payload.get('email')
        email_verified = token_payload.get('email_verified', False)
        google_sub = token_payload.get('sub')
        masked_email = _mask_email(email)

        if not email:
            logger.error(
                'Google OAuth failed: missing email in token',
                extra={
                    'outcome': 'missing_email',
                    'ip_address': ip_address,
                    'flow': flow,
                }
            )
            return Response(
                {'error': 'Google account email is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user = User.objects.filter(email__iexact=email).first()
        
        if flow == 'login':
            if user is None:
                logger.warning(
                    'Google OAuth login failed: account not found',
                    extra={
                        'outcome': 'account_not_found',
                        'ip_address': ip_address,
                        'masked_email': masked_email,
                        'flow': 'login',
                    }
                )
                return Response(
                    {'error': LOGIN_ACCOUNT_NOT_FOUND},
                    status=status.HTTP_404_NOT_FOUND,
                )
            # Check if user has linked Google account
            if not user.google_id:
                # User has password-only account
                if not email_verified:
                    logger.warning(
                        'Google OAuth login failed: email not verified',
                        extra={
                            'outcome': 'email_not_verified',
                            'ip_address': ip_address,
                            'masked_email': masked_email,
                            'flow': 'login',
                        }
                    )
                    return Response(
                        {'error': 'Google account email is not verified. '
                                 'Please verify your email with Google.'},
                        status=status.HTTP_401_UNAUTHORIZED,
                    )
                # Return 409 for password-only account
                logger.warning(
                    'Google OAuth login failed: password-only account exists',
                    extra={
                        'outcome': 'account_exists_password_only',
                        'ip_address': ip_address,
                        'masked_email': masked_email,
                        'flow': 'login',
                    }
                )
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
            logger.info(
                'Google OAuth login succeeded',
                extra={
                    'outcome': 'success',
                    'ip_address': ip_address,
                    'masked_email': masked_email,
                    'flow': 'login',
                }
            )
            return Response(self._build_token_payload(user), status=200)

        if user is not None:
            # Signup flow with existing user
            if user.google_id:
                # User already has Google linked, log them in
                logger.info(
                    'Google OAuth signup succeeded: existing Google-linked user',
                    extra={
                        'outcome': 'success_existing_user',
                        'ip_address': ip_address,
                        'masked_email': masked_email,
                        'flow': 'signup',
                    }
                )
                payload = self._build_token_payload(user)
                payload['is_existing_user'] = True
                payload['message'] = (
                    'Account already exists — you have been logged in.'
                )
                return Response(payload, status=status.HTTP_200_OK)
            # User has password-only account
            if not email_verified:
                logger.warning(
                    'Google OAuth signup failed: email not verified',
                    extra={
                        'outcome': 'email_not_verified',
                        'ip_address': ip_address,
                        'masked_email': masked_email,
                        'flow': 'signup',
                    }
                )
                return Response(
                    {'error': 'Google account email is not verified. '
                             'Please verify your email with Google.'},
                    status=status.HTTP_401_UNAUTHORIZED,
                )
            # Return 409 for password-only account
            logger.warning(
                'Google OAuth signup failed: password-only account exists',
                extra={
                    'outcome': 'account_exists_password_only',
                    'ip_address': ip_address,
                    'masked_email': masked_email,
                    'flow': 'signup',
                }
            )
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
        logger.info(
            'Google OAuth signup succeeded: new user created',
            extra={
                'outcome': 'success_new_user',
                'ip_address': ip_address,
                'masked_email': masked_email,
                'flow': 'signup',
            }
        )
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

        Nonce state is tracked in the cache to prevent replay attacks:
        - On first successful validation, the nonce is atomically consumed
        - Subsequent attempts with the same nonce are rejected
        - Nonces expire after NONCE_TTL_SECONDS (65 minutes)

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

            # Check if nonce has already been consumed (replay protection)
            # Use cache.add() which is atomic: returns True if key was added,
            # False if key already exists. This prevents race conditions.
            cache_key = f'oauth_nonce:{request_nonce}'
            nonce_already_used = not cache.add(
                cache_key,
                'consumed',
                timeout=NONCE_TTL_SECONDS
            )

            if nonce_already_used:
                logger.warning(
                    'Nonce replay detected in Google OAuth callback: '
                    'nonce has already been used.'
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
        ip_address = _get_client_ip(request)
        serializer = GoogleAuthSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            token_payload = verify_google_id_token(
                serializer.validated_data['id_token'],
            )
        except AuthenticationFailed as exc:
            logger.warning(
                'Google OAuth link failed: token verification failed',
                extra={
                    'outcome': 'token_verification_failed',
                    'ip_address': ip_address,
                    'flow': 'link',
                    'user_email': _mask_email(request.user.email),
                }
            )
            raise
        
        # Validate nonce for replay protection
        try:
            self._validate_nonce(
                serializer.validated_data.get('nonce'),
                token_payload,
            )
        except AuthenticationFailed as exc:
            logger.warning(
                'Google OAuth link failed: nonce validation failed',
                extra={
                    'outcome': 'nonce_validation_failed',
                    'ip_address': ip_address,
                    'flow': 'link',
                    'user_email': _mask_email(request.user.email),
                }
            )
            raise
        
        email = token_payload.get('email')
        email_verified = token_payload.get('email_verified', False)
        google_sub = token_payload.get('sub')
        masked_email = _mask_email(email)

        if not email:
            logger.error(
                'Google OAuth link failed: missing email in token',
                extra={
                    'outcome': 'missing_email',
                    'ip_address': ip_address,
                    'flow': 'link',
                    'user_email': _mask_email(request.user.email),
                }
            )
            return Response(
                {'error': 'Google account email is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not email_verified:
            logger.warning(
                'Google OAuth link failed: email not verified',
                extra={
                    'outcome': 'email_not_verified',
                    'ip_address': ip_address,
                    'masked_email': masked_email,
                    'flow': 'link',
                    'user_email': _mask_email(request.user.email),
                }
            )
            return Response(
                {'error': 'Google account email is not verified. '
                         'Please verify your email with Google.'},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        # Check if Google email matches current user's email (case-insensitive)
        if email.lower() != request.user.email.lower():
            logger.warning(
                'Google OAuth link failed: email mismatch',
                extra={
                    'outcome': 'email_mismatch',
                    'ip_address': ip_address,
                    'masked_email': masked_email,
                    'flow': 'link',
                    'user_email': _mask_email(request.user.email),
                }
            )
            return Response(
                {'error': 'Google account email does not match your account email.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Check if Google identity is already linked to another account
        existing_google_user = User.objects.filter(google_id=google_sub).first()
        if existing_google_user and existing_google_user != request.user:
            logger.warning(
                'Google OAuth link failed: identity already linked elsewhere',
                extra={
                    'outcome': 'google_identity_already_linked',
                    'ip_address': ip_address,
                    'masked_email': masked_email,
                    'flow': 'link',
                    'user_email': _mask_email(request.user.email),
                }
            )
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

        logger.info(
            'Google OAuth link succeeded',
            extra={
                'outcome': 'success',
                'ip_address': ip_address,
                'masked_email': masked_email,
                'flow': 'link',
                'user_email': _mask_email(request.user.email),
            }
        )

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

        Nonce state is tracked in the cache to prevent replay attacks:
        - On first successful validation, the nonce is atomically consumed
        - Subsequent attempts with the same nonce are rejected
        - Nonces expire after NONCE_TTL_SECONDS (65 minutes)

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
                raise AuthenticationFailed(
                    'Nonce validation failed. The provided nonce does not '
                    'match the token\'s nonce claim.'
                )

            # Check if nonce has already been consumed (replay protection)
            # Use cache.add() which is atomic: returns True if key was added,
            # False if key already exists. This prevents race conditions.
            cache_key = f'oauth_nonce:{request_nonce}'
            nonce_already_used = not cache.add(
                cache_key,
                'consumed',
                timeout=NONCE_TTL_SECONDS
            )

            if nonce_already_used:
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
