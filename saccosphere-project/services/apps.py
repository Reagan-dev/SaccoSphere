from django.apps import AppConfig


class ServicesConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'services'

    def ready(self):
        """Register signal handlers when the services app is ready."""
        import services.signals  # noqa: F401
