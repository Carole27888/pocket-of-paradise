from django.apps import AppConfig


class FrontDeskConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.frontdesk"
    label = "frontdesk"
    verbose_name = "Front Desk"

    def ready(self):
        from . import tasks  # noqa: F401
