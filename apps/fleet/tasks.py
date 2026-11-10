from apps.accounts import roles
from apps.core.jobs import daily, task


@daily(hour=7, minute=0)
@task("fleet.document_expiry_alerts")
def document_expiry_alerts():
    """Insurance / inspection / service due within 14 days → notify Operations + Admin."""
    from apps.accounts.models import User
    from apps.notifications.services import notify

    from .models import Vehicle

    recipients = list(User.objects.filter(is_active=True, groups__name__in=[roles.OPERATIONS, roles.ADMIN]).distinct())
    count = 0
    for v in Vehicle.objects.exclude(status=Vehicle.OUT_OF_SERVICE):
        for flag in v.expiry_flags(within_days=14):
            when = "overdue" if flag["overdue"] else f"due in {flag['days']} days"
            for u in recipients:
                n = notify(u, f"{v.registration_no}: {flag['label']} {when}",
                           f"{flag['label']} date {flag['date']:%d %b %Y}", url=f"/fleet/{v.pk}/",
                           level="danger" if flag["overdue"] else "warning",
                           dedupe_key=f"fleet:{v.pk}:{flag['label']}:{flag['date']}", push=False)
                count += bool(n)
    return f"{count} notifications"
