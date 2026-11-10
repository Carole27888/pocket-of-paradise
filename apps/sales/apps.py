from django.apps import AppConfig


class SalesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.sales"
    label = "sales"
    verbose_name = "Sales"

    def ready(self):
        from . import tasks  # noqa: F401  (registers scheduled jobs)
