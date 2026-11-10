from django.utils import timezone

from apps.accounts import roles
from apps.core.jobs import daily, every, task

DESK_ROLES = [*sorted(roles.FRONTDESK_ROLES), roles.OPERATIONS, roles.ADMIN]


def _desk_users(extra_roles=()):
    from apps.accounts.models import User

    return list(User.objects.filter(is_active=True, groups__name__in=[*DESK_ROLES, *extra_roles]).distinct())


@every(minutes=10)
@task("frontdesk.late_return_alerts")
def late_return_alerts():
    """Vehicles or crew past their expected return → notify the desk, Operations, Admin and whoever checked them out."""
    from apps.core.templatetags.ds import ds_datetime
    from apps.notifications.services import notify

    from .models import TripMovement

    recipients = _desk_users()
    count = 0
    for mv in (TripMovement.objects.late().filter(late_alerted_at__isnull=True)
               .select_related("vehicle", "driver", "guide", "destination", "checked_out_by")):
        crew = ", ".join(p.full_name for p in mv.crew) or "no crew"
        body = f"{mv.destination_label} · {crew} · expected back {ds_datetime(mv.expected_return_at)}"
        users = {u.pk: u for u in recipients}
        if mv.checked_out_by and mv.checked_out_by.is_active:
            users[mv.checked_out_by.pk] = mv.checked_out_by
        for u in users.values():
            n = notify(u, f"Late return: {mv.subject_label}", body, url=mv.get_absolute_url(), level="danger",
                       dedupe_key=f"late:{mv.pk}")
            count += bool(n)
        mv.late_alerted_at = timezone.now()
        mv.save(update_fields=["late_alerted_at", "updated_at"])
    return f"{count} notifications"


@daily(hour=18, minute=30)
@task("frontdesk.daily_desk_report")
def daily_desk_report(day: str = ""):
    """Evening summary of the desk's day for Admin, Directors, Reservations and Operations."""
    from datetime import date

    from apps.notifications.services import notify

    from .services import desk_day_summary

    d = date.fromisoformat(day) if day else timezone.localdate()
    s = desk_day_summary(d)
    body = (f"{s['total']} desk enquir{'y' if s['total'] == 1 else 'ies'} ({s['unassigned']} unassigned), "
            f"{len(s['out_today'])} checked out, {len(s['back_today'])} back, {len(s['still_out'])} still out"
            + (f", {len(s['late'])} late" if s['late'] else "") + ".")
    count = 0
    for u in _desk_users([roles.DIRECTOR]):
        n = notify(u, f"Daily desk report · {d.day} {d:%b}", body, url=f"/frontdesk/report/?date={d.isoformat()}",
                   level="info", dedupe_key=f"deskreport:{d.isoformat()}", push=False)
        count += bool(n)
    return body
