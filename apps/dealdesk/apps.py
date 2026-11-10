from django.apps import AppConfig


class DealdeskConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.dealdesk"
    label = "dealdesk"
    verbose_name = "Deal Desk"

    def ready(self):
        from . import tasks  # noqa: F401
