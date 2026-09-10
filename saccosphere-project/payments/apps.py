from django.apps import AppConfig


class PaymentsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'payments'

    def ready(self):
        """Register app-specific startup checks."""
        from django.core.checks import register

        from .checks import check_mpesa_callback_token

        register(check_mpesa_callback_token)
