from django.apps import AppConfig


class FruitApiConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'fruit_app'

    def ready(self):
        # Fail at startup, not on the first request, when hardware_params.toml is invalid.
        from . import views
        views._ensure_capture_timing_config()
