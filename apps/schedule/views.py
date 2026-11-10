import calendar as cal
from datetime import date, datetime, timedelta

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts import roles as R
from apps.accounts.models import User
from apps.accounts.permissions import module_required

from . import services
from .models import DEPARTMENTS, CalendarEvent, EventInvite

DEPT_MODULE = {"sales": "sales", "frontdesk": "frontdesk", "trips": "trips", "finance": "finance", "marketing": "marketing",
               "production": "production", "hr": "hr", "executive": "admin"}


def _departments(user):
    from apps.core.modules import user_can_access_module

    out = [("mine", "My calendar")]
    for key, label in DEPARTMENTS:
        if key == "all":
            continue
        if user.is_superuser or user.has_role(*R.ALL_ACCESS) or (key != "executive" and user_can_access_module(user, DEPT_MODULE.get(key, key))):
            out.append((key, label))
    return out


def _auto_items(user, dept, start, end):
    """Things other modules already know about, shown read-only (departures for Trips)."""
    items = []
    if dept in ("trips", "mine") and (dept == "trips" or user.has_role(R.GUIDE)):
        from apps.trips.views import visible_departures

        for d in visible_departures(user).exclude(stage="cancelled").filter(start_date__lt=end.date(), end_date__gte=start.date()).select_related("package"):
            items.append({"date": d.start_date, "title": f"{d.reference} · {d.package.title}", "url": d.get_absolute_url(), "kind": "trip"})
    return items


@module_required("me", "ict")
def calendar_view(request):
    today = timezone.localdate()
    try:
        first = date(int(request.GET.get("year", today.year)), int(request.GET.get("month", today.month)), 1)
    except ValueError:
        first = today.replace(day=1)
    depts = _departments(request.user)
    dept = request.GET.get("dept", "mine")
    if dept not in dict(depts):
        dept = "mine"
    last = first.replace(day=cal.monthrange(first.year, first.month)[1])
    start = timezone.make_aware(datetime.combine(first, datetime.min.time()))
    end = timezone.make_aware(datetime.combine(last + timedelta(days=1), datetime.min.time()))
    events = list(services.visible_events(request.user, start, end, dept).select_related("owner"))
    by_day = {}
    for e in events:
        d0 = e.local_date
        d1 = timezone.localtime(e.end).date() if e.end and e.all_day else d0
        d = d0
        while d <= min(d1, last):
            if d >= first:
                by_day.setdefault(d, []).append({"title": e.title, "url": e.get_absolute_url(), "kind": e.kind, "time": "" if e.all_day else f"{timezone.localtime(e.start):%I:%M %p}".lstrip("0")})
            d += timedelta(days=1)
    for it in _auto_items(request.user, dept, start, end):
        by_day.setdefault(it["date"], []).append({"title": it["title"], "url": it["url"], "kind": it["kind"], "time": ""})
    weeks = [[{"date": d, "in_month": d.month == first.month, "today": d == today, "items": by_day.get(d, [])} for d in week]
             for week in cal.Calendar(0).monthdatescalendar(first.year, first.month)]
    upcoming = [e for e in events if e.start >= timezone.now()][:8]
    pending = EventInvite.objects.filter(user=request.user, status="pending", event__start__gte=timezone.now() - timedelta(days=1)).select_related("event", "invited_by")
    return render(request, "schedule/calendar.html", {
        "weeks": weeks, "first": first, "prev": (first - timedelta(days=1)).replace(day=1), "next": last + timedelta(days=1), "dept": dept,
        "depts": depts, "upcoming": upcoming, "pending": pending, "kinds": CalendarEvent.KINDS,
        "dept_choices": [d for d in DEPARTMENTS], "can_book_ceo": request.user.has_role(R.FRONTDESK_MANAGER, R.RESERVATIONS, R.RECEPTIONIST, R.COO, R.ADMIN)})


@require_POST
@module_required("me")
def event_add(request):
    p = request.POST
    try:
        start = services.parse_local(p.get("start"))
        end = services.parse_local(p["end"]) if p.get("end") else None
        if not p.get("title", "").strip():
            raise ValueError("Give the event a title.")
        ev = services.create_event(request.user, title=p["title"], start=start, end=end, all_day=bool(p.get("all_day")),
                                   department=p.get("department", "all") if p.get("department") in dict(DEPARTMENTS) else "all",
                                   kind=p.get("kind", "meeting") if p.get("kind") in dict(CalendarEvent.KINDS) else "meeting",
                                   location=p.get("location", ""), notes=p.get("notes", ""))
        added = []
        for uid in p.getlist("invite"):
            u = User.objects.filter(pk=uid, is_active=True).first()
            if u and u.pk != request.user.pk:
                try:
                    inv = services.invite(ev, request.user, u)
                    added.append(f"{u.full_name}{' (needs to accept)' if inv.status == 'pending' else ''}")
                except ValueError:
                    pass
        messages.success(request, "Event added." + (f" Invited: {', '.join(added)}." if added else ""))
        return redirect(ev)
    except ValueError as exc:
        messages.error(request, str(exc))
    return redirect("schedule:calendar")


@module_required("me")
def event_detail(request, pk):
    ev = get_object_or_404(CalendarEvent.objects.select_related("owner"), pk=pk)
    mine = ev.owner_id == request.user.pk
    inv = ev.invites.filter(user=request.user).first()
    if not (mine or inv or ev.department == "all" or request.user.has_role(*R.ALL_ACCESS) or request.user.is_superuser):
        raise PermissionDenied
    people = User.objects.filter(is_active=True).exclude(pk=request.user.pk).order_by("full_name")
    return render(request, "schedule/event.html", {"ev": ev, "mine": mine and not ev.source, "invite": inv, "invites": ev.invites.select_related("user"),
                                                   "people": people, "my_rank": services.calendar_rank(request.user)})


@require_POST
@module_required("me")
def event_action(request, pk):
    ev = get_object_or_404(CalendarEvent, pk=pk)
    a = request.POST.get("action")
    if a in ("accept", "decline"):
        inv = get_object_or_404(EventInvite, event=ev, user=request.user)
        services.respond(inv, request.user, a == "accept")
        messages.success(request, "Invitation " + ("accepted." if a == "accept" else "declined."))
        return redirect("schedule:calendar")
    if ev.owner_id != request.user.pk or ev.source:           # only the organiser edits, and auto-created events are managed by their module
        raise PermissionDenied
    if a == "delete":
        ev.delete()
        messages.success(request, "Event deleted.")
        return redirect("schedule:calendar")
    if a == "invite":
        u = User.objects.filter(pk=request.POST.get("user") or 0, is_active=True).first()
        if u:
            try:
                inv = services.invite(ev, request.user, u)
                messages.success(request, f"{u.full_name} invited" + (": they must accept first." if inv.status == "pending" else " and added to their calendar."))
            except ValueError as exc:
                messages.error(request, str(exc))
    elif a == "update":
        try:
            ev.title = request.POST.get("title", ev.title).strip()[:160] or ev.title
            ev.start = services.parse_local(request.POST.get("start"))
            ev.end = services.parse_local(request.POST["end"]) if request.POST.get("end") else None
            if ev.end and ev.end < ev.start:
                raise ValueError("The event can't end before it starts.")
            ev.location, ev.notes = request.POST.get("location", "")[:120], request.POST.get("notes", "")
            ev.save()
            messages.success(request, "Event updated.")
        except ValueError as exc:
            messages.error(request, str(exc))
    return redirect(ev)


@require_POST
@module_required("me")
def book_ceo(request):
    ceo = User.objects.filter(groups__name=R.DIRECTOR, is_active=True).first()
    try:
        if ceo is None:
            raise ValueError("There is no CEO account to book.")
        ev = services.book_ceo(request.user, ceo, title=request.POST.get("title", "").strip() or "Meeting with the CEO",
                               start=services.parse_local(request.POST.get("start")),
                               end=services.parse_local(request.POST["end"]) if request.POST.get("end") else None, notes=request.POST.get("notes", ""))
        messages.success(request, "Booked straight onto the CEO's calendar.")
        return redirect(ev)
    except PermissionError as exc:
        raise PermissionDenied from exc
    except ValueError as exc:
        messages.error(request, str(exc))
    return redirect("schedule:calendar")
