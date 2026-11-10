from django.utils import timezone

from apps.core.jobs import daily, task


@daily(hour=1, minute=30)
@task("hr.leave_year_rollover")
def leave_year_rollover():
    """On the first day of a new leave year, carry unused annual leave forward (capped by policy)."""
    from . import services
    from .models import LeavePolicy

    today = timezone.localdate()
    if today.month == LeavePolicy.get().year_start_month and today.day == 1:
        return f"{services.carry_forward(services.leave_year(today))} carried forward"
    return "not a rollover day"


@daily(hour=8, minute=10)
@task("hr.pending_leave_reminders")
def pending_leave_reminders():
    """Nudge approvers about leave requests waiting more than two days."""
    from datetime import timedelta

    from apps.notifications.services import notify

    from . import services
    from .models import LeaveRequest

    n = 0
    for r in LeaveRequest.objects.filter(status="pending", created_at__lt=timezone.now() - timedelta(days=2)).select_related("employee"):
        for a in services.approvers_for(r.employee):
            n += bool(notify(a, f"Leave still waiting: {r.employee.full_name}", f"{r.leave_type.name} from {r.start_date:%d %b}", "/hr/approvals/", "warning",
                             dedupe_key=f"leave-wait:{r.pk}:{a.pk}:{timezone.localdate():%Y-%m-%d}"))
    return f"{n} reminders"
