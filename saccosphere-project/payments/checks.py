"""Django system checks for payments app configuration."""

from django.conf import settings
from django.core.checks import Error


def check_mpesa_callback_token(app_configs, **kwargs):
    """
    Verify MPESA_CALLBACK_TOKEN is set when DEBUG is False.

    The M-Pesa callback endpoints validate an unguessable token embedded in
    the callback URL path (payments/views.py, payments/disbursements.py,
    payments/withdrawals.py). Each call site guards that check with
    ``if token:``, so when the setting is blank the path-token check is
    silently skipped and the endpoints are left protected only by the
    Safaricom IP allowlist.

    This check is a deploy-time guard: it fails fast at `manage.py check` /
    process startup so a production deploy cannot ship without the token. It
    is skipped when DEBUG is True so local development is not blocked.
    """
    if settings.DEBUG:
        return []

    if not getattr(settings, 'MPESA_CALLBACK_TOKEN', ''):
        return [
            Error(
                'MPESA_CALLBACK_TOKEN is not set while DEBUG is False.',
                hint=(
                    'Set MPESA_CALLBACK_TOKEN to a long, unguessable value '
                    'in your production environment. Without it the M-Pesa '
                    'callback endpoints fall back to IP-allowlist-only '
                    'protection.'
                ),
                id='payments.E001',
            ),
        ]

    return []
