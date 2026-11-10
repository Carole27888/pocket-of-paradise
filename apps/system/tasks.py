from datetime import timedelta

from django.utils import timezone

from apps.core.jobs import daily, task
from apps.notifications.services import notify


@daily(hour=8, minute=0)
@task("system.plan_week_reminder")
def plan_week_reminder():
    """Monday morning: anyone with nothing planned for the week gets a nudge (the page also shows a Required banner)."""
    from apps.accounts.models import User
    from apps.sales.models import SalesSettings

    from .models import WeeklyPlanItem as P

    today = timezone.localdate()
    if today.weekday() != 0 or not SalesSettings.get().auto_plan_reminders:
        return "not Monday or reminders off"
    n = 0
    for u in User.objects.filter(is_active=True).exclude(employee__isnull=True):
        if not P.objects.filter(user=u, day__range=(today, today + timedelta(days=6))).exists():
            n += bool(notify(u, "Plan your week", "Add what you will do each day this week on My To-Do. It is required.", "/system/todo/", "warning", dedupe_key=f"plan-week:{today}"))
    return f"{n} reminded"


@daily(hour=16, minute=30)
@task("system.plan_mark_reminder")
def plan_mark_reminder():
    """Weekday 4:30pm: anyone with unmarked planned items for today (or earlier) is asked to mark how the day went."""
    from apps.accounts.models import User
    from apps.sales.models import SalesSettings

    from .models import WeeklyPlanItem as P

    today = timezone.localdate()
    if today.weekday() > 4 or not SalesSettings.get().auto_plan_reminders:
        return "weekend or reminders off"
    n = 0
    for u in User.objects.filter(is_active=True, plan_items__status=P.PLANNED, plan_items__day__lte=today).distinct():
        left = P.objects.filter(user=u, status=P.PLANNED, day__lte=today).count()
        n += bool(notify(u, "Mark how your day went", f"{left} planned item{'s are' if left != 1 else ' is'} not marked yet: done, partly done or not done.", "/system/todo/", "info", dedupe_key=f"plan-mark:{today}"))
    return f"{n} reminded"
