from django.apps import AppConfig


class IctConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.ict"
    label = "ict"
    verbose_name = "ICT & AI"

    def ready(self):
        from . import tasks  # noqa: F401
