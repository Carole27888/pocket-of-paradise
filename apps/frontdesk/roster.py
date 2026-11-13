"""Weekend attendance roster: the Front Desk Manager picks who comes in on each Saturday and Sunday. Each person is alerted (app and
WhatsApp), sees it on My To-Do and gets a reminder on Friday afternoon. The page shows who actually clocked in."""
from datetime import datetime, timedelta

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts import roles as R
from apps.accounts.permissions import module_required
from apps.people.models import Employee

from .models import WeekendShift

WEEKS = 6


def can_plan(user) -> bool:
    return bool(user.is_superuser or user.has_role(R.ADMIN, R.COO, R.FRONTDESK_MANAGER))


def weekends(start=None, weeks=WEEKS) -> list:
    """Saturdays and Sundays from this weekend (or last one, if today is Monday) for `weeks` weekends."""
    today = start or timezone.localdate()
    sat = today + timedelta(days=(5 - today.weekday()) % 7)
    if today.weekday() == 6:                 # Sunday: this weekend began yesterday
        sat = today - timedelta(days=1)
    return [(sat + timedelta(days=7 * i), sat + timedelta(days=7 * i + 1)) for i in range(weeks)]


def _time(v, default):
    try:
        return datetime.strptime(v, "%H:%M").time()
    except (TypeError, ValueError):
        return default


def _tell(shift, removed=False):
    from apps.notifications.services import notify

    u = shift.employee.user
    if not u:
        return
    if removed:
        notify(u, f"Taken off the weekend roster: {shift.date:%a %d %b}", "You are no longer expected in that day.", "/system/todo/", "info")
    else:
        notify(u, f"You're on the weekend roster: {shift.date:%a %d %b}", f"{shift.start_time:%H:%M}–{shift.end_time:%H:%M}" + (f" · {shift.note}" if shift.note else ""),
               "/system/todo/", "info", dedupe_key=f"wkshift:{shift.pk}")


@module_required("frontdesk")
def weekend_roster(request):
    plan = can_plan(request.user)
    if request.method == "POST":
        if not plan:
            raise PermissionDenied
        act = request.POST.get("action")
        if act == "add":
            try:
                day = datetime.strptime(request.POST.get("date", ""), "%Y-%m-%d").date()
            except ValueError:
                messages.error(request, "Pick the day.")
                return redirect("frontdesk:weekend_roster")
            if day.weekday() < 5:
                messages.error(request, "The weekend roster is for Saturdays and Sundays.")
                return redirect("frontdesk:weekend_roster")
            start, end = _time(request.POST.get("start_time"), None), _time(request.POST.get("end_time"), None)
            start, end = start or WeekendShift._meta.get_field("start_time").default, end or WeekendShift._meta.get_field("end_time").default
            if end <= start:
                messages.error(request, "The finish time must be after the start time.")
                return redirect("frontdesk:weekend_roster")
            added, already = [], []
            for emp in Employee.objects.filter(pk__in=request.POST.getlist("employees"), is_active=True).select_related("user"):
                shift, created = WeekendShift.objects.get_or_create(date=day, employee=emp, defaults={
                    "start_time": start, "end_time": end, "note": request.POST.get("note", "")[:160], "created_by": request.user})
                if created:
                    _tell(shift)
                    added.append(emp.full_name)
                else:
                    already.append(emp.full_name)
            if added:
                messages.success(request, f"{', '.join(added)} on for {day:%a %d %b} and told on WhatsApp / the app.")
            if already:
                messages.info(request, f"Already on that day: {', '.join(already)}.")
            if not added and not already:
                messages.error(request, "Tick at least one person.")
        elif act == "remove":
            shift = get_object_or_404(WeekendShift, pk=request.POST.get("pk"))
            _tell(shift, removed=True)
            shift.delete()
            messages.success(request, f"{shift.employee.full_name} taken off {shift.date:%a %d %b}.")
        elif act == "copy":                          # same people as the weekend before, on the chosen weekend
            try:
                sat = datetime.strptime(request.POST.get("saturday", ""), "%Y-%m-%d").date()
            except ValueError:
                return redirect("frontdesk:weekend_roster")
            n = 0
            for d in (sat, sat + timedelta(days=1)):
                for old in WeekendShift.objects.filter(date=d - timedelta(days=7)).select_related("employee__user"):
                    shift, created = WeekendShift.objects.get_or_create(date=d, employee=old.employee, defaults={
                        "start_time": old.start_time, "end_time": old.end_time, "note": old.note, "created_by": request.user})
                    if created:
                        _tell(shift)
                        n += 1
            messages.success(request, f"Copied {n} shift{'s' if n != 1 else ''} from the weekend before." if n else "Nothing to copy from the weekend before.")
        return redirect("frontdesk:weekend_roster")
    from apps.hr.models import AttendanceRecord

    pairs = weekends()
    days = [d for pair in pairs for d in pair]
    shifts = list(WeekendShift.objects.filter(date__in=days).select_related("employee__user"))
    clocked = {(a.user_id, a.date): a for a in AttendanceRecord.objects.filter(date__in=days)}
    today = timezone.localdate()
    by_day = {d: [] for d in days}
    for s in shifts:
        a = clocked.get((s.employee.user_id, s.date)) if s.employee.user_id else None
        s.came = a
        s.past = s.date < today or (s.date == today and timezone.localtime().time() > s.start_time)
        by_day[s.date].append(s)
    rows = [{"sat": sat, "sun": sun, "sat_shifts": by_day[sat], "sun_shifts": by_day[sun]} for sat, sun in pairs]
    staff = Employee.objects.filter(is_active=True).select_related("department").order_by("full_name")
    return render(request, "frontdesk/weekend_roster.html", {"rows": rows, "plan": plan, "staff": staff, "today": today,
                                                             "default_start": "09:00", "default_end": "17:00"})


def my_shifts(user, days=21):
    """The person's own coming weekend shifts, for My To-Do."""
    emp = getattr(user, "employee", None)
    if emp is None:
        return []
    today = timezone.localdate()
    return list(WeekendShift.objects.filter(employee=emp, date__gte=today, date__lte=today + timedelta(days=days)))
