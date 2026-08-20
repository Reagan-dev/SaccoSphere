import logging

from django.apps import AppConfig
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


logger = logging.getLogger(__name__)


class AccountsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'accounts'

    def ready(self):
        """Run app-specific startup checks."""
        # Prevent OAUTH_MOCK=True in production
        if not settings.DEBUG and settings.OAUTH_MOCK:
            raise ImproperlyConfigured(
                'OAUTH_MOCK=True is not allowed when DEBUG=False. '
                'Set OAUTH_MOCK=False in your Railway environment variables '
                'before deploying to production.'
            )
        if settings.OTP_HASH_KEY_USES_SECRET_KEY_FALLBACK:
            logger.warning(
                'OTP_HASH_KEY is not set. Falling back to a key derived from '
                'SECRET_KEY. Set a dedicated OTP_HASH_KEY in production.'
            )
        # Ensure Africa's Talking credentials are set in production
        if not settings.DEBUG and not settings.AT_API_KEY:
            raise ImproperlyConfigured(
                'AT_API_KEY must be set when DEBUG=False. '
                'Set AT_API_KEY in your Railway environment variables '
                'before deploying to production.'
            )
        if not settings.DEBUG and not settings.AT_USERNAME:
            raise ImproperlyConfigured(
                'AT_USERNAME must be set when DEBUG=False. '
                'Set AT_USERNAME in your Railway environment variables '
                'before deploying to production.'
            )
