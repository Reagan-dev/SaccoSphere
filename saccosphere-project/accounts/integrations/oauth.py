"""Google OAuth integration for SaccoSphere."""

import logging

import requests
from django.conf import settings


logger = logging.getLogger('saccosphere.oauth')


class OAuthError(Exception):
    """Raised when Google OAuth cannot complete successfully."""

    pass


class GoogleOAuthClient:
    """Client for Google OAuth authorization-code and userinfo calls."""

    TIMEOUT_SECONDS = 8

    def __init__(self):
        self.client_id = settings.GOOGLE_OAUTH_CLIENT_ID
        self.client_secret = settings.GOOGLE_OAUTH_CLIENT_SECRET
        self.redirect_uri = settings.GOOGLE_OAUTH_REDIRECT_URI
        self.token_url = settings.GOOGLE_OAUTH_TOKEN_URL
        self.userinfo_url = settings.GOOGLE_OAUTH_USERINFO_URL
        self.mock = settings.DEBUG or settings.OAUTH_MOCK

    def exchange_code_for_token(self, auth_code):
        """
        Exchange an authorization code for a Google OAuth token response.
        """
        if self.mock:
            return {
                'access_token': 'mock_access_token_12345',
                'token_type': 'Bearer',
                'expires_in': 3600,
                'refresh_token': 'mock_refresh_token_67890',
                'scope': 'openid email profile',
            }

        self._validate_configuration()
        payload = {
            'code': auth_code,
            'client_id': self.client_id,
            'client_secret': self.client_secret,
            'redirect_uri': self.redirect_uri,
            'grant_type': 'authorization_code',
        }

        try:
            response = requests.post(
                self.token_url,
                data=payload,
                timeout=self.TIMEOUT_SECONDS,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            logger.warning('Google OAuth token exchange failed: %s', exc)
            raise OAuthError('Google OAuth token exchange failed.') from exc

        try:
            data = response.json()
        except ValueError as exc:
            raise OAuthError('Google OAuth token response was invalid.') from exc

        if 'access_token' not in data:
            raise OAuthError('Google OAuth token response had no access token.')

        return data

    def get_user_info(self, access_token):
        """Return the Google user profile for an OAuth access token."""
        if self.mock:
            return {
                'email': 'user@example.com',
                'given_name': 'John',
                'family_name': 'Doe',
                'picture': 'https://example.com/avatar.jpg',
            }

        headers = {'Authorization': f'Bearer {access_token}'}
        try:
            response = requests.get(
                self.userinfo_url,
                headers=headers,
                timeout=self.TIMEOUT_SECONDS,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            logger.warning('Google OAuth userinfo request failed: %s', exc)
            raise OAuthError('Google OAuth userinfo request failed.') from exc

        try:
            data = response.json()
        except ValueError as exc:
            raise OAuthError(
                'Google OAuth userinfo response was invalid.'
            ) from exc

        if 'email' not in data:
            raise OAuthError('Google OAuth userinfo response had no email.')

        return data

    def _validate_configuration(self):
        missing = [
            name for name, value in (
                ('GOOGLE_OAUTH_CLIENT_ID', self.client_id),
                ('GOOGLE_OAUTH_CLIENT_SECRET', self.client_secret),
                ('GOOGLE_OAUTH_REDIRECT_URI', self.redirect_uri),
                ('GOOGLE_OAUTH_TOKEN_URL', self.token_url),
            )
            if not value
        ]
        if missing:
            raise OAuthError(
                'Google OAuth is missing settings: ' + ', '.join(missing)
            )
