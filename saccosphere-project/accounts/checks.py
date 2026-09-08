"""Django system checks for accounts app configuration."""

from django.conf import settings
from django.core.checks import Error


def check_field_encryption_key(app_configs, **kwargs):
    """
    Verify FIELD_ENCRYPTION_KEY is present and a valid Fernet key.

    EncryptedCharField (accounts/models.py) only validates this lazily, on
    first field read/write, so a missing or malformed key would otherwise
    pass startup/health checks and only surface once payment config is
    touched. This check fails fast at `manage.py check` / process startup
    time instead.
    """
    from cryptography.fernet import Fernet

    errors = []
    key = getattr(settings, 'FIELD_ENCRYPTION_KEY', None)

    if not key:
        errors.append(
            Error(
                'FIELD_ENCRYPTION_KEY is not set.',
                hint=(
                    'Set FIELD_ENCRYPTION_KEY in your environment/settings. '
                    'Generate one with: from cryptography.fernet import '
                    'Fernet; Fernet.generate_key()'
                ),
                id='accounts.E001',
            ),
        )
        return errors

    try:
        Fernet(key.encode() if isinstance(key, str) else key)
    except Exception as exc:
        errors.append(
            Error(
                f'FIELD_ENCRYPTION_KEY is not a valid Fernet key: {exc}',
                hint=(
                    'FIELD_ENCRYPTION_KEY must be a 32-byte URL-safe '
                    'base64-encoded key. Generate one with: from '
                    'cryptography.fernet import Fernet; '
                    'Fernet.generate_key()'
                ),
                id='accounts.E002',
            ),
        )

    return errors
