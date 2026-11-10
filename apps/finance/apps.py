from django.apps import AppConfig


class FinanceConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.finance"
    label = "finance"
    verbose_name = "Finance"

    def ready(self):
        from apps.trips import services as trip_services

        from . import signals, tasks  # noqa: F401  (registers signals and scheduled jobs)
        from .services import release_prepayments

        trip_services.COMPLETION_HOOKS.append(lambda dep, user: release_prepayments(dep, user))
