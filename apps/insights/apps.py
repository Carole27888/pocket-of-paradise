from django.apps import AppConfig


class InsightsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.insights"
    label = "insights"
    verbose_name = "Insights"

    def ready(self):
        from . import tasks  # noqa: F401  (registers the competitor checks)
