from datetime import timedelta

from django.utils import timezone

from apps.accounts import roles
from apps.core.jobs import daily, monthly, task
from apps.notifications.services import notify_roles


@monthly(day=2, hour=3)
@task("compliance.retention_run")
def retention_run():
    from .services import run_retention

    return run_retention()


@daily(hour=8, minute=45)
@task("compliance.deadline_alerts")
def deadline_alerts():
    """Data requests due within 5 days, overdue ones, and breaches nearing their 72-hour reporting window."""
    from .models import DataBreach, DataRequest

    today = timezone.localdate()
    n = 0
    for r in DataRequest.objects.exclude(status="closed").filter(due_on__lte=today + timedelta(days=5)):
        n += notify_roles([roles.ADMIN, roles.COO], f"Data request {r.reference} {'overdue' if r.is_overdue else 'due ' + r.due_on.strftime('%d %b')}",
                          f"{r.get_kind_display()} from {r.requester_name}", r.get_absolute_url(), "danger" if r.is_overdue else "warning",
                          dedupe_key=f"dsr:{r.pk}:{today:%Y-%m-%d}")
    for b in DataBreach.objects.filter(reported_at__isnull=True).exclude(severity="low"):
        if timezone.now() > b.detected_at + timedelta(hours=48):
            n += notify_roles([roles.ADMIN, roles.COO, roles.DIRECTOR], f"Breach {b.reference} not yet reported",
                              "72-hour reporting window " + ("has passed" if b.report_overdue else "closes soon"), b.get_absolute_url(), "danger",
                              dedupe_key=f"breach:{b.pk}:{today:%Y-%m-%d}")
    return f"{n} alerts"
