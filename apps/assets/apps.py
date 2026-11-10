from django.apps import AppConfig


class AssetsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.assets"
    label = "assets"
    verbose_name = "Fixed assets"

    def ready(self):
        from . import tasks  # noqa: F401
