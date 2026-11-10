from django.apps import AppConfig


class MarketingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.marketing"
    label = "marketing"
    verbose_name = "Marketing"

    def ready(self):
        from . import tasks  # noqa: F401
        from .services import register_hooks

        register_hooks()
