from django.apps import AppConfig


class CoachConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.coach"
    label = "coach"
    verbose_name = "My assistant"

    def ready(self):
        from . import tasks  # noqa: F401
