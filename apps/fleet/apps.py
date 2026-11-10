from django.apps import AppConfig


class FleetConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.fleet"
    label = "fleet"
    verbose_name = "Fleet"

    def ready(self):
        from . import tasks  # noqa: F401
