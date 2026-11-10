from django.apps import AppConfig


class TripsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.trips"
    label = "trips"
    verbose_name = "Trip Readiness"

    def ready(self):
        from . import tasks  # noqa: F401  (registers scheduled jobs)
