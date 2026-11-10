from django.apps import AppConfig


class SystemConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.system"
    label = "system"
    verbose_name = "System"

    def ready(self):
        from . import tasks  # noqa: F401  (registers scheduled jobs)
