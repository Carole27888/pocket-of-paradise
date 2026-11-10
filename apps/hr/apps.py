from django.apps import AppConfig


class HrConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.hr"
    label = "hr"
    verbose_name = "People & Leave"

    def ready(self):
        from . import tasks  # noqa: F401
