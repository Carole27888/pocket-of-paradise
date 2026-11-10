from apps.core.jobs import monthly, task


@monthly(day=1, hour=4)
@task("assets.monthly_depreciation")
def monthly_depreciation():
    from datetime import timedelta

    from django.utils import timezone

    from .services import post_depreciation

    last = (timezone.localdate().replace(day=1) - timedelta(days=1)).strftime("%Y-%m")   # post the month just ended
    run = post_depreciation(last)
    return f"{run.period}: {run.amount}"
