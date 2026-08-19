from django.apps import AppConfig
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


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
