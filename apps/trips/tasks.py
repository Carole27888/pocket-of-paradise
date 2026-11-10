from datetime import timedelta

from django.utils import timezone

from apps.accounts import roles
from apps.core.jobs import daily, task
from apps.notifications.services import notify_roles

from .models import Departure, Stage


@daily(hour=8, minute=0)
@task("trips.departure_alerts")
def departure_alerts():
    """Stuck departures, missing manifests and unpaid park fees 48 hours out → Operations."""
    today = timezone.localdate()
    n = 0
    for dep in Departure.objects.filter(stage__in=[Stage.PLANNED, Stage.READY, Stage.DEPARTED]).select_related("package"):
        reasons = dep.attention()
        if 0 <= dep.days_to_departure <= 2 and dep.stage in (Stage.PLANNED, Stage.READY):
            if not dep.park_fees_paid and "Park fees paid" not in " ".join(reasons):
                reasons.append("Park fees unpaid with 48 hours to go")
        if reasons:
            n += notify_roles([roles.OPERATIONS, roles.ADMIN], f"{dep.reference} needs attention · {dep.package.title}",
                              "; ".join(reasons)[:280], dep.get_absolute_url(), "warning", dedupe_key=f"dep-attn:{dep.pk}:{today:%Y-%m-%d}")
    return f"{n} alerts"


@daily(hour=8, minute=30)
@task("trips.prepayment_alerts")
def prepayment_alerts():
    """Departures within a week with no prepaid supplier bill: nudge Finance and the Supplier Coordinator."""
    today = timezone.localdate()
    n = 0
    for dep in Departure.objects.filter(start_date__range=(today, today + timedelta(days=7)), stage__in=[Stage.PLANNED, Stage.READY]).select_related("package"):
        if not dep.bills.filter(prepayment=True).exclude(status="void").exists():
            n += notify_roles([roles.FINANCE, roles.SUPPLIER_COORDINATOR], f"Prepayment due: {dep.reference}",
                              f"{dep.package.title} leaves {dep.start_date:%d %b} with no prepaid supplier bill.",
                              f"/suppliers/bills/new/?departure={dep.pk}", "warning", dedupe_key=f"prepay:{dep.pk}:{today:%Y-%m-%d}")
    return f"{n} alerts"
