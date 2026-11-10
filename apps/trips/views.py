import json
from collections import Counter, defaultdict
from datetime import timedelta

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import Avg, Count, F, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts import roles
from apps.accounts.permissions import module_required
from apps.core.crud import CreateScreen, UpdateScreen
from apps.core.tables import Col, TableScreen
from apps.sales.models import Booking, BookingStatus

from . import services
from .forms import (AssignResourcesForm, CancelForm, ChecklistForm, DelayForm, DepartureForm, IncidentForm,
                    ManifestEntryForm, TripExpenseForm)
from .models import CHECKLIST, STAGE_TONE, Departure, Incident, ManifestEntry, Stage

TRIPS = "trips"


def visible_departures(user):
    qs = Departure.objects.select_related("package", "guide", "driver", "vehicle")
    if user.has_role(roles.GUIDE) and not user.has_role(roles.ADMIN, roles.DIRECTOR, roles.OPERATIONS,
                                                       roles.FRONTDESK_MANAGER, roles.DISPATCHER):
        qs = qs.filter(Q(guide__user=user) | Q(driver__user=user))
    return qs


def _need_manage(user):
    if not user.can_manage_trips:
        raise PermissionDenied


def _departure(request, pk, *, manage=False):
    dep = get_object_or_404(visible_departures(request.user), pk=pk)
    if manage:
        _need_manage(request.user)
    return dep


def _flash(request, fn, *args, ok="Done.", **kw):
    try:
        res = fn(*args, **kw)
        messages.success(request, ok)
        return res
    except ValueError as exc:
        messages.error(request, str(exc))
        return None


# ------------------------------------------------------------------------------------------------ overview
@module_required(TRIPS)
def overview(request):
    qs = visible_departures(request.user)
    today = timezone.localdate()
    live = qs.exclude(stage=Stage.CANCELLED)
    counts = Counter(live.values_list("stage", flat=True))
    planned_total = sum(counts.values()) or 1
    done_t = live.filter(departed_at__isnull=False, departed_at__gt=F("created_at")).annotate(
        d=F("departed_at") - F("created_at")).aggregate(a=Avg("d"))["a"]
    horizon = live.filter(stage__in=[Stage.PLANNED, Stage.READY, Stage.DEPARTED]).order_by("start_date")
    week = horizon.filter(start_date__gte=today, start_date__lte=today + timedelta(days=7))
    attention = []
    for d in horizon:
        reasons = d.attention()
        if reasons:
            attention.append({"dep": d, "reasons": reasons})
    stack = [("Waiting on resources", counts[Stage.PLANNED], "warning"), ("Ready to depart", counts[Stage.READY], "info"),
             ("On the road", counts[Stage.DEPARTED], "success"), ("Completed", counts[Stage.COMPLETED], "done")]
    return render(request, "trips/overview.html", {
        "counts": counts, "total": sum(counts.values()), "stack": [(l, n, t, round(100 * n / planned_total))
                                                                     for l, n, t in stack],
        "ready_pct": round(100 * (counts[Stage.READY] + counts[Stage.DEPARTED] + counts[Stage.COMPLETED]) / planned_total),
        "departed_pct": round(100 * (counts[Stage.DEPARTED] + counts[Stage.COMPLETED]) / planned_total),
        "avg_days": round(done_t.total_seconds() / 86400, 1) if done_t else None,
        "progress": horizon[:25], "attention": attention[:8], "attention_count": len(attention), "week": week[:10],
        "can_manage": request.user.can_manage_trips, "today": today,
        "open_incidents": Incident.objects.filter(resolved_at__isnull=True, departure__in=qs).count(),
    })


@module_required(TRIPS)
def board(request):
    qs = visible_departures(request.user).exclude(stage=Stage.CANCELLED)
    cutoff = timezone.localdate() - timedelta(days=14)
    cols = []
    for st in (Stage.PLANNED, Stage.READY, Stage.DEPARTED, Stage.COMPLETED):
        items = qs.filter(stage=st)
        items = items.filter(end_date__gte=cutoff) if st == Stage.COMPLETED else items
        cols.append({"stage": st, "items": list(items.order_by("start_date")[:30]), "tone": STAGE_TONE[st.value]})
    return render(request, "trips/board.html", {"cols": cols, "can_manage": request.user.can_manage_trips})


# ------------------------------------------------------------------------------------------------ list & CRUD
class DepartureList(TableScreen):
    module_key = TRIPS
    model = Departure
    tabs_field = "stage"
    tabs_choices = Stage.choices
    search_fields = ["reference", "package__title", "package__code", "guide__full_name", "vehicle__registration_no"]
    sort_fields = ["start_date", "stage", "capacity"]
    default_sort = "start_date"
    title = "Departures"
    subtitle = "Every dated run of a tour: guide, vehicle, seats and readiness."
    placeholder = "Search reference, tour, guide, vehicle…"
    export_name = "departures"
    columns = [
        Col("Departure", "reference", sort="reference", link=True, sub="package.title"),
        Col("Dates", "start_date", sort="start_date", kind="date", sub="end_date"),
        Col("Seats", "seats_label"),
        Col("Guide", "guide", sub="vehicle"),
        Col("Readiness", "checklist_percent", kind="pct"),
        Col("Stage", "get_stage_display", kind="pill", tone=lambda o: STAGE_TONE[o.stage]),
    ]

    def get_base_queryset(self):
        self._base = visible_departures(self.request.user)
        qs = self._base
        self._unfiltered = qs
        val = self.request.GET.get("stage", "")
        return qs.filter(stage=val) if val else qs

    def actions(self):
        return [{"label": "New departure", "url": reverse("trips:create"), "style": "accent", "icon": "plus"}] \
            if self.request.user.can_manage_trips else []

    def kpis(self, qs):
        up = qs.filter(start_date__gte=timezone.localdate(), stage__in=[Stage.PLANNED, Stage.READY])
        return [{"label": "Upcoming", "value": up.count(), "accent": True},
                {"label": "Next 7 days", "value": up.filter(start_date__lte=timezone.localdate() + timedelta(days=7)).count()},
                {"label": "On the road", "value": qs.filter(stage=Stage.DEPARTED).count()},
                {"label": "Seats sold (upcoming)", "value": sum(d.seats_sold for d in up[:200])}]


# seats_label is a plain attribute used by the table


class DepartureCreate(CreateScreen):
    module_key = TRIPS
    edit_permission = "can_manage_trips"
    form_class = DepartureForm
    model = Departure
    title = "New departure"
    cancel_url_name = "trips:list"

    def get_initial(self):
        init = super().get_initial()
        for k in ("package",):
            if self.request.GET.get(k, "").isdigit():
                init[k] = int(self.request.GET[k])
        return init


class DepartureUpdate(UpdateScreen):
    module_key = TRIPS
    edit_permission = "can_manage_trips"
    form_class = DepartureForm
    model = Departure


@module_required(TRIPS)
def detail(request, pk):
    dep = _departure(request, pk)
    bookings = dep.live_bookings.select_related("client", "agent").prefetch_related("payments")
    from apps.sales.models import Booking as B

    candidates = (B.objects.filter(departure__isnull=True, package=dep.package)
                  .exclude(status=BookingStatus.CANCELLED).select_related("client")[:30])
    return render(request, "trips/detail.html", {
        "dep": dep, "bookings": bookings, "events": dep.events.select_related("user")[:20],
        "can_manage": request.user.can_manage_trips, "attention": dep.attention(),
        "assign_form": AssignResourcesForm(initial={"guide": dep.guide_id, "driver": dep.driver_id,
                                                    "vehicle": dep.vehicle_id}),
        "check_form": ChecklistForm(initial={f: getattr(dep, f) for f, _ in CHECKLIST}),
        "delay_form": DelayForm(initial={"reason": dep.delay_reason}), "cancel_form": CancelForm(),
        "candidates": candidates, "incidents": dep.incidents.all()[:5],
        "stages": [Stage.PLANNED, Stage.READY, Stage.DEPARTED, Stage.COMPLETED],
        "manifest_count": dep.manifest.count(), "checked_in": dep.manifest.filter(checked_in_at__isnull=False).count(),
    })


@require_POST
@module_required(TRIPS)
def assign(request, pk):
    dep = _departure(request, pk, manage=True)
    form = AssignResourcesForm(request.POST)
    if form.is_valid():
        _flash(request, services.assign_resources, dep, request.user, ok="Resources updated.", **form.cleaned_data)
    return redirect(dep)


@require_POST
@module_required(TRIPS)
def checklist(request, pk):
    dep = _departure(request, pk, manage=True)
    form = ChecklistForm(request.POST)
    if form.is_valid():
        services.set_checklist(dep, request.user, **form.cleaned_data)
        messages.success(request, "Checklist saved.")
    return redirect(dep)


@require_POST
@module_required(TRIPS)
def advance(request, pk):
    dep = _departure(request, pk, manage=True)
    action = request.POST.get("action", "")
    fn = {"ready": (services.mark_ready, "Resources confirmed."), "depart": (services.mark_departed, "Marked as departed."),
          "complete": (services.mark_completed, "Trip completed.")}.get(action)
    if action == "cancel":
        form = CancelForm(request.POST)
        if form.is_valid():
            _flash(request, services.cancel, dep, request.user, form.cleaned_data["reason"], ok="Departure cancelled.")
        else:
            messages.error(request, "Give a cancellation reason.")
    elif action == "delay":
        services.set_delay(dep, request.POST.get("reason", "").strip(), request.user)
        messages.success(request, "Delay updated.")
    elif fn:
        _flash(request, fn[0], dep, request.user, ok=fn[1])
    if request.htmx:
        resp = HttpResponse(status=204)
        resp["HX-Refresh"] = "true"
        return resp
    return redirect(request.POST.get("next") or dep)


@require_POST
@module_required(TRIPS)
def attach(request, pk):
    dep = _departure(request, pk, manage=True)
    booking = get_object_or_404(Booking, pk=request.POST.get("booking") or 0)
    _flash(request, services.attach_booking, booking, dep, request.user, ok=f"{booking.reference} added.")
    return redirect(dep)


# ------------------------------------------------------------------------------------------------ manifest
@module_required(TRIPS)
def manifest(request, pk):
    dep = _departure(request, pk)
    if request.GET.get("sync") == "1" and request.user.can_manage_trips:
        n = services.sync_manifest(dep)
        messages.success(request, f"{n} guest(s) added from bookings." if n else "Manifest already up to date.")
        return redirect("trips:manifest", pk=pk)
    entries = list(dep.manifest.select_related("booking"))
    ctx = {"dep": dep, "entries": entries, "checked": sum(1 for e in entries if e.checked_in_at),
           "can_manage": request.user.can_manage_trips, "can_checkin": request.user.can_manage_trips or request.user.has_role(roles.GUIDE),
           "pii": request.user.can_manage_trips or request.user.has_role(roles.GUIDE, roles.ADMIN)}
    if request.GET.get("print") == "1":
        from apps.core.models import CompanySetting

        ctx["company"] = CompanySetting.load()
        return render(request, "trips/manifest_print.html", ctx)
    return render(request, "trips/manifest.html", ctx)


@require_POST
@module_required(TRIPS)
def manifest_check(request, pk, entry_id):
    dep = _departure(request, pk)
    if not (request.user.can_manage_trips or request.user.has_role(roles.GUIDE)):
        raise PermissionDenied
    entry = get_object_or_404(dep.manifest, pk=entry_id)
    services.check_in(entry, request.user, undo=request.POST.get("undo") == "1")
    return render(request, "trips/partials/manifest_row.html", {"e": entry, "dep": dep, "can_checkin": True,
                                                                   "pii": True})


@require_POST
@module_required(TRIPS)
def manifest_check_all(request, pk):
    dep = _departure(request, pk)
    if not (request.user.can_manage_trips or request.user.has_role(roles.GUIDE)):
        raise PermissionDenied
    n = services.check_in_all(dep, request.user)
    messages.success(request, f"{n} guest(s) checked in.")
    return redirect("trips:manifest", pk=pk)


@module_required(TRIPS)
def manifest_edit(request, pk, entry_id=None):
    dep = _departure(request, pk, manage=True)
    obj = get_object_or_404(dep.manifest, pk=entry_id) if entry_id else None
    form = ManifestEntryForm(request.POST or None, instance=obj)
    if request.method == "POST" and form.is_valid():
        e = form.save(commit=False)
        e.departure = dep
        e.save()
        messages.success(request, "Guest saved.")
        return redirect("trips:manifest", pk=pk)
    return render(request, "components/form_page.html", {
        "form": form, "title": "Edit guest" if obj else "Add guest", "subtitle": dep.reference,
        "cancel_url": reverse("trips:manifest", args=[pk])})


# ------------------------------------------------------------------------------------------------ guide trip expenses
@module_required(TRIPS)
def expense_new(request, pk):
    """A guide or driver submits a trip expense from the field: it lands in Finance as Pending."""
    dep = _departure(request, pk)
    if not (request.user.can_manage_trips or request.user.has_role(roles.GUIDE)):
        raise PermissionDenied
    form = TripExpenseForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        from apps.finance.services import record_expense

        d = form.cleaned_data
        exp = record_expense(request.user, date=timezone.localdate(), category=d["category"], payee=f"{dep.guide or 'Guide'} (field)",
                             description=f"{dep.reference}: {d['description']}", amount=d["amount"], method=d["method"], departure=dep,
                             vehicle=dep.vehicle, receipt_file=d["receipt"] or "")
        services.log_event(dep, "expense", f"Expense submitted: {exp.get_category_display()} KES {exp.amount:,.0f}", request.user)
        messages.success(request, f"Expense {exp.number} sent to Finance for approval" + ("" if exp.has_receipt else " (receipt missing)."))
        return redirect(dep)
    return render(request, "components/form_page.html", {"form": form, "title": "Trip expense", "subtitle": f"{dep.reference} · {dep.package.title}",
                                                         "cancel_url": dep.get_absolute_url()})


# ------------------------------------------------------------------------------------------------ incidents
@module_required(TRIPS)
def incident_new(request, pk):
    dep = _departure(request, pk)
    if not (request.user.can_manage_trips or request.user.has_role(roles.GUIDE)):
        raise PermissionDenied
    form = IncidentForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        inc = services.report_incident(dep, request.user, **form.cleaned_data)
        messages.success(request, "Incident reported.")
        return redirect(inc)
    return render(request, "components/form_page.html", {
        "form": form, "title": "Report an incident", "subtitle": f"{dep.reference} · {dep.package.title}",
        "cancel_url": dep.get_absolute_url()})


@module_required(TRIPS)
def incident(request, pk):
    inc = get_object_or_404(Incident.objects.filter(departure__in=visible_departures(request.user))
                            .select_related("departure__package", "guide"), pk=pk)
    if request.method == "POST":
        _need_manage(request.user)
        services.resolve_incident(inc, request.user, request.POST.get("note", ""))
        messages.success(request, "Incident resolved.")
        return redirect(inc)
    return render(request, "trips/incident.html", {"inc": inc, "can_manage": request.user.can_manage_trips})


class IncidentList(TableScreen):
    module_key = TRIPS
    model = Incident
    search_fields = ["description", "departure__reference", "departure__package__title"]
    sort_fields = ["reported_at", "severity"]
    default_sort = "-reported_at"
    title = "Incidents"
    subtitle = "Everything that went wrong on the road, and how it was handled."
    empty_title = "No incidents reported"
    export_name = "incidents"
    columns = [
        Col("Incident", "get_kind_display", link=True, sub="departure"),
        Col("Reported", "reported_at", sort="reported_at", kind="datetime"),
        Col("Guide", "guide"),
        Col("Severity", "get_severity_display", kind="pill",
            tone=lambda o: {"low": "neutral", "medium": "warning", "high": "danger"}[o.severity]),
        Col("Status", "status_label", kind="pill", tone=lambda o: "warning" if o.is_open else "success"),
    ]

    def get_base_queryset(self):
        return Incident.objects.filter(departure__in=visible_departures(self.request.user)).select_related(
            "departure__package", "guide")

    def kpis(self, qs):
        return [{"label": "Open", "value": qs.filter(resolved_at__isnull=True).count(), "accent": True},
                {"label": "High severity (open)", "value": qs.filter(resolved_at__isnull=True, severity="high").count()},
                {"label": "This month", "value": qs.filter(reported_at__date__gte=timezone.localdate().replace(day=1)).count()},
                {"label": "Total", "value": qs.count()}]




# ------------------------------------------------------------------------------------------------ reports
@module_required(TRIPS)
def reports(request):
    if not request.user.has_role(roles.ADMIN, roles.DIRECTOR, roles.OPERATIONS, roles.FRONTDESK_MANAGER):
        raise PermissionDenied
    today = timezone.localdate()
    qs = Departure.objects.exclude(stage=Stage.CANCELLED)
    weeks = []
    for i in range(7, -1, -1):
        start = today - timedelta(days=today.weekday() + 7 * i)
        end = start + timedelta(days=6)
        w = qs.filter(start_date__range=(start, end))
        weeks.append({"label": f"{start:%d %b}", "planned": w.count(),
                      "ready": w.exclude(stage=Stage.PLANNED).count(),
                      "departed": w.filter(stage__in=[Stage.DEPARTED, Stage.COMPLETED]).count(),
                      "completed": w.filter(stage=Stage.COMPLETED).count()})
    done = qs.filter(stage__in=[Stage.DEPARTED, Stage.COMPLETED], departed_at__isnull=False)
    on_time = sum(1 for d in done if timezone.localtime(d.departed_at).date() <= d.start_date)
    stage_time = {}
    ready = qs.filter(resources_confirmed_at__isnull=False)
    if ready:
        stage_time["Planned → ready"] = round(sum((d.resources_confirmed_at - d.created_at).total_seconds() for d in ready) / len(ready) / 86400, 1)
    if done:
        stage_time["Ready → departed"] = round(sum((d.departed_at - (d.resources_confirmed_at or d.created_at)).total_seconds() for d in done) / len(done) / 86400, 1)
    comp = qs.filter(completed_at__isnull=False, departed_at__isnull=False)
    if comp:
        stage_time["Departed → completed"] = round(sum((d.completed_at - d.departed_at).total_seconds() for d in comp) / len(comp) / 86400, 1)
    by_type = defaultdict(lambda: [0, 0])
    for d in qs.select_related("package"):
        k = d.package.get_package_type_display()
        by_type[k][0] += 1
        by_type[k][1] += d.stage == Stage.COMPLETED
    trips_done = max(qs.filter(stage__in=[Stage.DEPARTED, Stage.COMPLETED]).count(), 1)
    from apps.fleet.models import Vehicle

    fleet = Vehicle.objects.exclude(status=Vehicle.OUT_OF_SERVICE).count() or 1
    in_use = Vehicle.objects.filter(status=Vehicle.OUT).count()
    team = (qs.exclude(guide__isnull=True).values("guide__full_name").annotate(trips=Count("pk"),
            done=Count("pk", filter=Q(stage=Stage.COMPLETED))).order_by("-trips")[:8])
    charts = {"weekly": {"labels": [w["label"] for w in weeks], "planned": [w["planned"] for w in weeks],
                         "ready": [w["ready"] for w in weeks], "departed": [w["departed"] for w in weeks],
                         "completed": [w["completed"] for w in weeks]}}
    return render(request, "trips/reports.html", {
        "charts": charts, "weeks": weeks, "on_time_pct": round(100 * on_time / len(done)) if done else None,
        "stage_time": stage_time, "by_type": [(k, v[0], v[1]) for k, v in by_type.items()],
        "incidents_per_100": round(100 * Incident.objects.count() / trips_done, 1),
        "fleet_util": round(100 * in_use / fleet), "team": team,
        "total": qs.count(), "completed": qs.filter(stage=Stage.COMPLETED).count()})
