from django.apps import AppConfig


class ComplianceConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.compliance"
    label = "compliance"
    verbose_name = "Data Protection"

    def ready(self):
        from . import tasks  # noqa: F401
