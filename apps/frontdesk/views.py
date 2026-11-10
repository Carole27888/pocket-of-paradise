import csv
import json
from datetime import date, timedelta

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import Count
from django.db.models.functions import ExtractHour
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.permissions import module_required
from apps.core.lists import ListScreen
from apps.fleet.models import Vehicle
from apps.leads import services as lead_services
from apps.leads.models import Enquiry, Source
from apps.people.models import Employee

from . import services
from .filters import MovementFilter, QueueFilter
from .forms import CheckInForm, CheckOutForm, QuickCaptureForm
from .models import DESK_SOURCES, TripMovement

MOVEMENT_RELATED = ("vehicle", "driver", "guide", "destination", "enquiry__client", "checked_out_by")


def _require_movement(user):
    if not user.can_run_movement:
        raise PermissionDenied


def _require_desk(user):
    if not user.can_run_desk:
        raise PermissionDenied


def _require_distribute(user):
    """Only Front Desk, Sales Manager, COO and CEO hand leads to agents."""
    if not user.can_distribute_leads:
        raise PermissionDenied


def _channel_labels():
    labels = __import__('apps.leads.models', fromlist=['x']).source_labels()
    return [(code, labels[code]) for code in DESK_SOURCES]


@module_required("frontdesk")
def dashboard(request):
    from apps.sales.services import pick_agent

    now = timezone.now()
    today = timezone.localdate()
    start, end = services.day_bounds(today)
    desk = services.desk_enquiries()
    todays = desk.filter(created_at__gte=start, created_at__lt=end)
    per_channel = dict(todays.values_list("source").annotate(n=Count("pk")))
    from apps.accounts.roles import SOCIAL_SOURCES

    channels = [{"code": c, "label": label, "count": per_channel.get(c, 0)} for c, label in _channel_labels()]
    other_n = sum(v for k, v in per_channel.items() if k not in list(DESK_SOURCES) + SOCIAL_SOURCES)
    extra_tiles = [{"code": "social", "label": "Social", "count": sum(per_channel.get(k, 0) for k in SOCIAL_SOURCES), "icon": "share"},
                   {"code": "other", "label": "Web & other", "count": other_n, "icon": "external"}]
    waiting_qs = services.filter_state(desk, "waiting").filter(created_at__gte=now - timedelta(days=14)).order_by("created_at")
    waiting = []
    for e in waiting_qs[:6]:
        agent, _method, why = pick_agent(e)
        waiting.append({"e": e, "suggested": agent, "why": why})
    board = services.agent_board()
    from apps.frontdesk.models import ShiftNote

    ctx = {
        "channels": channels, "extra_tiles": extra_tiles, "can_run_desk": request.user.can_run_desk, "can_distribute": request.user.can_distribute_leads,
        "kpis": {"today": todays.count(), "waiting": waiting_qs.count(), "quotes": todays.filter(stage__in=["quoted", "hot", "itinerary"]).count(),
                 "in_office": sum(1 for r in board if r["agent"].is_available)},
        "waiting": waiting, "board": board, "agents": _assignable_agents(),
        "todays": todays.order_by("-created_at")[:10], "notes": ShiftNote.objects.all()[:4],
    }
    return render(request, "frontdesk/dashboard.html", ctx)


@module_required("frontdesk")
def new_lead(request):
    from apps.notifications.services import notify

    _require_desk(request.user)
    form = QuickCaptureForm(request.POST or None, initial={"source": request.GET.get("source", "whatsapp")})
    board = services.agent_board()
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        pkg = d["package"]
        enquiry = lead_services.create_enquiry(
            client_data={"full_name": d["full_name"], "phone": d["phone"], "email": request.POST.get("email", "").strip(), "nationality": ""},
            enquiry_data={"source": d["source"], "purpose": d["purpose"], "package": pkg, "destination": pkg.destinations.first() if pkg else None,
                          "destination_text": d["destination_text"], "adults": d["adults"], "children": d["children"], "notes": d["notes"],
                          "budget_text": request.POST.get("budget", "")[:60], "captured_via": "front_desk",
                          "travel_date_from": request.POST.get("travel_date") or None},
            user=request.user, auto_assign=False)
        agent = d["assigned_agent"] if request.user.can_distribute_leads else None
        if request.POST.get("submit") == "assign" and agent is None and request.user.can_distribute_leads:
            from apps.sales.services import maybe_auto_assign, pick_agent

            picked, method, why = pick_agent(enquiry)
            agent = picked
            method_why = (method, why)
        else:
            method_why = ("manual", "Initial assignment")
        if agent and request.POST.get("submit") != "later":
            try:
                lead_services.assign_agent(enquiry, agent, request.user, *method_why)
                if request.POST.get("sms_agent") and agent.user_id:   # the agent already gets the new-lead alert (app + WhatsApp); this adds an SMS
                    if agent.phone:
                        from apps.integrations.messaging import send_message

                        send_message("sms", agent.phone, f"New lead {enquiry.reference}: {enquiry.client.full_name} {enquiry.client.phone}", purpose="lead_agent_alert", related=f"enquiry:{enquiry.pk}")
            except ValueError as exc:
                messages.warning(request, f"{enquiry.reference} saved but not assigned: {exc}")
        if request.POST.get("wa_client"):
            from apps.integrations.messaging import send_message

            send_message("whatsapp", enquiry.client.phone, f"Hello {enquiry.client.full_name.split()[0]}, thank you for contacting Pocket of Paradise Travel Company. A travel consultant will be in touch shortly.",
                         purpose="lead_welcome", related=f"enquiry:{enquiry.pk}")
        messages.success(request, f"{enquiry.reference} captured for {enquiry.client.full_name}" + (" (returning client)." if enquiry.is_returning_client else "."))
        if request.POST.get("another"):
            return redirect(f"{reverse('frontdesk:new_lead')}?source={d['source']}")
        return redirect("frontdesk:queue")
    return render(request, "frontdesk/new_lead.html", {"form": form, "board": board, "can_distribute": request.user.can_distribute_leads, "sources": [(c, l) for c, l in _channel_labels()]})


@require_POST
@module_required("frontdesk")
def assign_lead(request, pk):
    """Assign from the dashboard's waiting list, then return to where the user was."""
    _require_distribute(request.user)
    enquiry = get_object_or_404(services.desk_enquiries(), pk=pk)
    agent = Employee.objects.filter(pk=request.POST.get("agent") or 0, is_sales_agent=True, is_active=True).first()
    if agent is None:
        messages.error(request, "Choose an agent.")
    else:
        try:
            lead_services.assign_agent(enquiry, agent, request.user, "manual", "Assigned from the desk dashboard")
            messages.success(request, f"{enquiry.reference} assigned to {agent.short_name}.")
        except ValueError as exc:
            messages.error(request, str(exc))
    nxt = request.POST.get("next") or ""
    return redirect(nxt if nxt.startswith("/frontdesk/") else "frontdesk:dashboard")


@module_required("frontdesk")
def suggest_agent(request, pk):
    """JSON for the Assign pop-up: who the rules would pick for this lead, and why."""
    from django.http import JsonResponse

    from apps.sales.services import pick_agent

    e = get_object_or_404(Enquiry, pk=pk)
    agent, _method, why = pick_agent(e)
    return JsonResponse({"agent": agent.pk if agent else None, "name": agent.full_name if agent else "", "why": why})


@require_POST
@module_required("frontdesk")
def remind_agent(request, pk):
    from apps.notifications.services import notify

    _require_desk(request.user)
    e = get_object_or_404(services.desk_enquiries(), pk=pk)
    if e.assigned_agent_id and e.assigned_agent.user_id:
        notify(e.assigned_agent.user, f"Please contact {e.client.full_name}", f"{e.reference} has not been contacted yet", e.get_absolute_url(), "warning")
        messages.success(request, f"Reminder sent to {e.assigned_agent.short_name}.")
    else:
        messages.error(request, "This lead has no agent to remind.")
    return redirect(request.POST.get("next") if (request.POST.get("next") or "").startswith("/frontdesk/") else "frontdesk:queue")


@module_required("frontdesk")
def calendar(request):
    from apps.schedule.views import calendar_view

    request.GET = request.GET.copy()
    request.GET["dept"] = "frontdesk"
    return calendar_view(request)


@module_required("frontdesk")
def manual(request):
    from apps.manuals.views import manual as manual_view

    return manual_view(request, "frontdesk")


class QueueScreen(ListScreen):
    """Capture queues: one tab per desk channel, quick capture at the top, waiting enquiries first."""

    module_key = "frontdesk"
    model = Enquiry
    filterset_class = QueueFilter
    search_fields = ["reference", "client__full_name", "client__phone", "destination_text", "package__title"]
    sort_fields = ["created_at", "client__full_name", "stage"]
    default_sort = "-created_at"
    template_name = "frontdesk/queue.html"
    partial_template = "frontdesk/partials/queue_table.html"
    title = "Leads Log"
    subtitle = "Every lead from every channel lands here first. Hand each one to an agent, and see who has not been contacted yet."
    export_name = "desk-queue"
    export_columns = [("Reference", "reference"), ("Captured", "created_at"), ("Channel", "get_source_display"),
                      ("Client", "client.full_name"), ("Phone", "client.phone"), ("Interest", "interest_label"),
                      ("Party", "party_label"), ("Agent", "assigned_agent"), ("Stage", "get_stage_display"),
                      ("Last contact", "last_contact_at")]

    @property
    def channel(self):
        c = self.request.GET.get("channel", "")
        return c if c in DESK_SOURCES or c in ("social", "other") else ""

    @staticmethod
    def _group(key):
        from apps.accounts.roles import SOCIAL_SOURCES

        return {"social": SOCIAL_SOURCES, "other": None}.get(key)

    def get_base_queryset(self):
        from apps.accounts.roles import SOCIAL_SOURCES

        qs = services.desk_enquiries()
        if self.channel == "social":
            return qs.filter(source__in=SOCIAL_SOURCES)
        if self.channel == "other":
            return qs.exclude(source__in=list(DESK_SOURCES) + SOCIAL_SOURCES)
        return qs.filter(source=self.channel) if self.channel else qs

    def get_context_data(self, form=None, **kw):
        ctx = super().get_context_data(**kw)
        period_qs = self.filterset.filter_period(services.desk_enquiries(), "period",
                                                 self.filterset.form.cleaned_data.get("period") or "all")
        counts = dict(period_qs.values_list("source").annotate(n=Count("pk")))
        waiting = dict(services.filter_state(period_qs, "waiting").values_list("source").annotate(n=Count("pk")))
        from apps.accounts.roles import SOCIAL_SOURCES

        def grp(d, keys):
            return sum(v for k, v in d.items() if k in keys)

        other = [k for k in counts if k not in list(DESK_SOURCES) + SOCIAL_SOURCES]
        ctx["tabs"] = [{"key": "", "label": "All channels", "count": sum(counts.values()), "waiting": sum(waiting.values())}] + [
            {"key": c, "label": label, "count": counts.get(c, 0), "waiting": waiting.get(c, 0)} for c, label in _channel_labels()] + [
            {"key": "social", "label": "Social", "count": grp(counts, SOCIAL_SOURCES), "waiting": grp(waiting, SOCIAL_SOURCES), "icon": "share"},
            {"key": "other", "label": "Web & other", "count": grp(counts, other), "waiting": grp(waiting, other), "icon": "external"}]
        ctx["channel"] = self.channel
        base = self.get_base_queryset()
        ctx["state_tabs"] = [("", "All", base.count()), ("contacted", "Contacted", services.filter_state(base, "contacted").count()),
                             ("assigned", "Not contacted", services.filter_state(base, "assigned").count()),
                             ("waiting", "Unassigned", services.filter_state(base, "waiting").count())]
        ctx["state"] = self.request.GET.get("state", "")
        ctx["form"] = form or QuickCaptureForm(initial={"source": self.channel or "whatsapp"})
        ctx["can_run_desk"] = self.request.user.can_run_desk
        ctx["can_distribute"] = self.request.user.can_distribute_leads
        ctx["agents"] = _assignable_agents()
        ctx["board"] = services.agent_board()
        return ctx

    def post(self, request, *args, **kwargs):
        _require_desk(request.user)
        form = QuickCaptureForm(request.POST)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form))
        d = form.cleaned_data
        pkg = d["package"]
        enquiry = lead_services.create_enquiry(
            client_data={"full_name": d["full_name"], "phone": d["phone"], "email": "", "nationality": ""},
            enquiry_data={"source": d["source"], "purpose": d["purpose"], "package": pkg,
                          "destination": pkg.destinations.first() if pkg else None,
                          "destination_text": d["destination_text"], "adults": d["adults"],
                          "children": d["children"], "notes": d["notes"], "captured_via": "front_desk"},
            user=request.user,
        )
        if d["assigned_agent"] and request.user.can_distribute_leads:
            try:
                lead_services.assign_agent(enquiry, d["assigned_agent"], request.user, "manual", "Initial assignment")
            except ValueError as exc:
                messages.warning(request, f"{enquiry.reference} saved but not assigned: {exc}")
        messages.success(request, f"{enquiry.reference} captured for {enquiry.client.full_name}"
                         + (" (returning client)." if enquiry.is_returning_client else "."))
        return redirect(f"{reverse('frontdesk:queue')}?channel={d['source']}")


# ---------------------------------------------------------------------------------------------- trip movement
@module_required("frontdesk")
def movement_board(request):
    open_mv = list(TripMovement.objects.open().select_related(*MOVEMENT_RELATED).order_by("expected_return_at"))
    today_end = services.day_bounds(timezone.localdate())[1]
    ctx = {
        "late": [m for m in open_mv if m.is_late],
        "due_today": [m for m in open_mv if not m.is_late and m.expected_return_at < today_end],
        "later": [m for m in open_mv if m.expected_return_at >= today_end],
        "open_count": len(open_mv),
        "available_vehicles": Vehicle.objects.filter(status=Vehicle.AVAILABLE).select_related("current_driver"),
        "unavailable_vehicles": Vehicle.objects.exclude(status__in=[Vehicle.AVAILABLE, Vehicle.OUT]),
        "available_drivers": services.available_crew("driver"),
        "available_guides": services.available_crew("guide"),
        "recent": TripMovement.objects.filter(checked_in_at__isnull=False).select_related(*MOVEMENT_RELATED)
        .order_by("-checked_in_at")[:6],
        "can_run_desk": request.user.can_run_desk,
        "can_run_movement": request.user.can_run_movement,
    }
    return render(request, "frontdesk/movement_board.html", ctx)


class MovementHistory(ListScreen):
    module_key = "frontdesk"
    model = TripMovement
    filterset_class = MovementFilter
    search_fields = ["reference", "vehicle__registration_no", "vehicle__nickname", "driver__full_name",
                     "guide__full_name", "destination__name", "destination_text", "enquiry__reference"]
    sort_fields = ["checked_out_at", "expected_return_at", "checked_in_at", "vehicle__registration_no"]
    default_sort = "-checked_out_at"
    template_name = "frontdesk/movement_history.html"
    partial_template = "frontdesk/partials/movement_table.html"
    title = "Movement log"
    subtitle = "Every guide, driver and vehicle check-out and check-in, with odometer and return times."
    export_name = "trip-movement"
    export_columns = [("Reference", "reference"), ("Vehicle", "vehicle"), ("Driver", "driver"), ("Guide", "guide"),
                      ("Purpose", "get_purpose_display"), ("Going to", "destination_label"),
                      ("Passengers", "passengers"), ("Out", "checked_out_at"), ("Expected back", "expected_return_at"),
                      ("Back", "checked_in_at"), ("Odometer out", "odometer_out"), ("Odometer in", "odometer_in"),
                      ("Km", "distance_km"), ("Condition", "get_condition_in_display"), ("Late", "returned_late")]

    def get_base_queryset(self):
        return TripMovement.objects.select_related(*MOVEMENT_RELATED)


@module_required("frontdesk")
def checkout(request):
    _require_movement(request.user)
    initial = {}
    v = None
    if request.GET.get("vehicle", "").isdigit():
        v = Vehicle.objects.filter(pk=request.GET["vehicle"], status=Vehicle.AVAILABLE).first()
    form = CheckOutForm(request.POST or None, initial=initial)
    if v:
        form.fields["vehicle"].initial = v.pk
        if v.current_driver_id and form.fields["driver"].queryset.filter(pk=v.current_driver_id).exists():
            form.fields["driver"].initial = v.current_driver_id
        form.fields["odometer_out"].widget.attrs["placeholder"] = f"{v.odometer_km}"
    for key in ("driver", "guide", "enquiry"):
        if request.GET.get(key, "").isdigit():
            form.fields[key].initial = int(request.GET[key])
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            mv = services.check_out(
                vehicle=d["vehicle"], driver=d["driver"], guide=d["guide"], user=request.user,
                checked_out_at=d["checked_out_at"], expected_return_at=d["expected_return_at"],
                odometer_out=d["odometer_out"], purpose=d["purpose"], destination=d["destination"],
                destination_text=d["destination_text"], enquiry=d["enquiry"], passengers=d["passengers"],
                fuel_out=d["fuel_out"], notes_out=d["notes_out"])
        except services.MovementError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, f"{mv.reference}: {mv.subject_label} checked out to {mv.destination_label}.")
            return redirect("frontdesk:movement")
    vehicle_odometers = {str(x.pk): x.odometer_km for x in form.fields["vehicle"].queryset}
    return render(request, "frontdesk/checkout.html", {"form": form, "vehicle_odometers": vehicle_odometers})


@module_required("frontdesk")
def movement_detail(request, pk):
    mv = get_object_or_404(TripMovement.objects.select_related(*MOVEMENT_RELATED, "checked_in_by"), pk=pk)
    form = None
    if mv.is_open and request.user.can_run_movement:
        form = CheckInForm(request.POST or None, movement=mv)
        if request.method == "POST" and form.is_valid():
            d = form.cleaned_data
            try:
                services.check_in(mv, user=request.user, checked_in_at=d["checked_in_at"],
                                  odometer_in=d.get("odometer_in"), fuel_in=d.get("fuel_in", ""),
                                  condition_in=d.get("condition_in") or TripMovement.CONDITION_OK,
                                  notes_in=d["notes_in"])
            except services.MovementError as exc:
                form.add_error(None, str(exc))
            else:
                mv.refresh_from_db()
                note = "" if mv.condition_in in ("", TripMovement.CONDITION_OK) else \
                    f" {mv.vehicle.registration_no} set to {mv.vehicle.get_status_display().lower()}."
                messages.success(request, f"{mv.reference}: {mv.subject_label} checked in.{note}")
                return redirect("frontdesk:movement")
    elif request.method == "POST":
        raise PermissionDenied
    return render(request, "frontdesk/movement_detail.html", {"mv": mv, "form": form})


# ---------------------------------------------------------------------------------------------- reports
@module_required("frontdesk")
def report(request):
    from apps.core.models import CompanySetting
    from apps.core.pdf import render_pdf

    period = request.GET.get("period") if request.GET.get("period") in ("daily", "weekly", "monthly") else "monthly"
    r = services.desk_period_report(period)
    ctx = {"r": r, "company": CompanySetting.load(), "generated": timezone.now(), "user": request.user,
           "charts": {"source": {"labels": [x["label"] for x in r["by_source"]], "contacted": [x["contacted"] for x in r["by_source"]], "waiting": [x["waiting"] for x in r["by_source"]]},
                      "weeks": {"labels": [w["label"] for w in r["weeks"]], "values": [w["n"] for w in r["weeks"]], "label": "Leads"}}}
    if request.GET.get("export") == "xlsx":
        from apps.reports.export import to_xlsx

        rows = [[x["label"], x["contacted"], x["waiting"]] for x in r["by_source"]]
        return to_xlsx(f"front-desk-{period}", ["Source", "Contacted", "Not yet contacted"], rows + [[], ["Agent", "Assigned", "Contacted"]] + [[a["name"], a["assigned"], a["contacted"]] for a in r["agents"]])
    if request.GET.get("export") == "pdf":
        return render_pdf(request, "frontdesk/report_pdf.html", ctx, f"front-desk-{period}.pdf", fallback_url=request.path)
    return render(request, "frontdesk/report.html", ctx)


@require_POST
@module_required("frontdesk")
def queue_assign(request, pk):
    """Inline 'Assign to' on a queue row. Returns the refreshed row."""
    _require_distribute(request.user)
    enquiry = get_object_or_404(services.desk_enquiries(), pk=pk)
    agent = Employee.objects.filter(pk=request.POST.get("agent") or 0, is_sales_agent=True, is_active=True).first()
    if agent is None:
        text, level = "Choose an agent.", "danger"
    else:
        try:
            lead_services.assign_agent(enquiry, agent, request.user, "manual", "Assigned from the desk queue")
            text, level = f"{enquiry.reference} assigned to {agent.short_name}.", "success"
        except ValueError as exc:
            text, level = str(exc), "danger"
    enquiry = services.desk_enquiries().get(pk=pk)
    resp = render(request, "frontdesk/partials/queue_row.html",
                  {"e": enquiry, "agents": _assignable_agents(), "can_run_desk": True, "can_distribute": True})
    resp["HX-Trigger"] = json.dumps({"toast": {"text": text, "level": level}})
    return resp


def _assignable_agents():
    return [a for a in Employee.objects.filter(is_sales_agent=True, is_active=True) if a.is_available]


# ------------------------------------------------------------------------------------------------ visitors, appointments, equipment
@module_required("frontdesk")
def visitors(request):
    from .models import Appointment, EquipmentRecord, ShiftNote, Visitor

    today = timezone.localdate()
    start, end = services.day_bounds(today)
    return render(request, "frontdesk/visitors.html", {
        "inside": Visitor.objects.filter(time_out__isnull=True), "today_visitors": Visitor.objects.filter(time_in__gte=start, time_in__lt=end),
        "appointments": Appointment.objects.filter(start__gte=start, start__lt=end).select_related("host"),
        "upcoming": Appointment.objects.filter(start__gte=end, start__lt=end + timedelta(days=30)).exclude(status="cancelled").select_related("host").order_by("start"),
        "equipment": EquipmentRecord.objects.select_related("assigned_to"), "agents": Employee.objects.filter(is_active=True),
        "notes": ShiftNote.objects.filter(date=today)[:5], "can_run_desk": request.user.can_run_desk,
        "overdue_equipment": [e for e in EquipmentRecord.objects.all() if e.state == "Overdue"]})


@require_POST
@module_required("frontdesk")
def visitor_in(request):
    from .models import Visitor

    _require_desk(request.user)
    name, reason = request.POST.get("name", "").strip(), request.POST.get("reason", "").strip()
    if not name or not reason:
        messages.error(request, "Name and reason are required.")
        return redirect("frontdesk:visitors")
    host = Employee.objects.filter(pk=request.POST.get("host") or 0).first()
    v = Visitor.objects.create(name=name[:120], phone_or_company=request.POST.get("contact", "")[:120], reason=reason[:200], host=host,
                               badge_no=request.POST.get("badge", "")[:10])
    if host and host.user_id:
        from apps.notifications.services import notify

        notify(host.user, f"{v.name} is at reception", v.reason, "/frontdesk/visitors/", "info", dedupe_key=f"visitor:{v.pk}")
    messages.success(request, f"{v.name} signed in.")
    return redirect("frontdesk:visitors")


@require_POST
@module_required("frontdesk")
def visitor_out(request, pk):
    from .models import Visitor

    _require_desk(request.user)
    v = get_object_or_404(Visitor, pk=pk)
    if v.is_in:
        v.time_out = timezone.now()
        v.save(update_fields=["time_out", "updated_at"])
    return redirect("frontdesk:visitors")


@require_POST
@module_required("frontdesk")
def appointment_add(request):
    from datetime import datetime

    from .models import Appointment

    _require_desk(request.user)
    try:
        start = timezone.make_aware(datetime.strptime(request.POST.get("start", ""), "%Y-%m-%dT%H:%M"))
    except ValueError:
        messages.error(request, "Choose a valid date and time.")
        return redirect("frontdesk:visitors")
    title = request.POST.get("title", "").strip()
    if not title:
        messages.error(request, "Give the appointment a title.")
        return redirect("frontdesk:visitors")
    host = Employee.objects.filter(pk=request.POST.get("host") or 0).first()
    Appointment.objects.create(title=title[:160], people=request.POST.get("people", "")[:200], host=host, start=start)
    messages.success(request, "Appointment added.")
    return redirect("frontdesk:visitors")


@require_POST
@module_required("frontdesk")
def appointment_update(request, pk):
    """Save the details pop-up: status, time, who is coming, host, place and notes."""
    from datetime import datetime

    from .models import Appointment

    _require_desk(request.user)
    a = get_object_or_404(Appointment, pk=pk)
    try:
        a.start = timezone.make_aware(datetime.strptime(request.POST.get("start", ""), "%Y-%m-%dT%H:%M"))
    except ValueError:
        messages.error(request, "Choose a valid date and time.")
        return redirect("frontdesk:visitors")
    a.title = request.POST.get("title", a.title).strip()[:160] or a.title
    a.people = request.POST.get("people", "")[:200]
    a.location = request.POST.get("location", "")[:120]
    a.notes = request.POST.get("notes", "")[:2000]
    a.host = Employee.objects.filter(pk=request.POST.get("host") or 0).first()
    if request.POST.get("status") in dict(Appointment.STATUSES):
        a.status = request.POST["status"]
    a.save()
    messages.success(request, "Appointment updated.")
    return redirect("frontdesk:visitors")


@require_POST
@module_required("frontdesk")
def appointment_status(request, pk):
    from .models import Appointment

    _require_desk(request.user)
    a = get_object_or_404(Appointment, pk=pk)
    if request.POST.get("status") in dict(Appointment.STATUSES):
        a.status = request.POST["status"]
        a.save(update_fields=["status", "updated_at"])
    return redirect("frontdesk:visitors")


@require_POST
@module_required("frontdesk")
def equipment_action(request):
    from datetime import timedelta as td

    from .models import EquipmentRecord

    _require_desk(request.user)
    eq = get_object_or_404(EquipmentRecord, pk=request.POST.get("id") or 0)
    if request.POST.get("action") == "issue":
        who = Employee.objects.filter(pk=request.POST.get("who") or 0).first()
        if eq.state != "In store":
            messages.error(request, f"{eq} is already out.")
        elif who is None:
            messages.error(request, "Choose who is taking it.")
        else:
            eq.assigned_to, eq.taken_at, eq.returned_at = who, timezone.now(), None
            eq.due_back = timezone.now() + td(days=max(1, int(request.POST.get("days", "1") or 1)))
            eq.save()
            messages.success(request, f"{eq} issued to {who.short_name}.")
    elif request.POST.get("action") == "return":
        eq.returned_at = timezone.now()
        eq.save(update_fields=["returned_at", "updated_at"])
        messages.success(request, f"{eq} returned to store.")
    return redirect("frontdesk:visitors")


@require_POST
@module_required("frontdesk")
def shift_note(request):
    from .models import ShiftNote

    _require_desk(request.user)
    text = request.POST.get("note", "").strip()
    if text:
        ShiftNote.objects.create(note=text[:1000], author_name=request.user.full_name)
        messages.success(request, "Shift note saved.")
    return redirect(request.POST.get("next") or "frontdesk:visitors")



# ------------------------------------------------------------------------------------------------ business cards
from apps.core.crud import CreateScreen, UpdateScreen  # noqa: E402
from apps.core.tables import Col, TableScreen  # noqa: E402

from .forms import BusinessCardForm  # noqa: E402
from .models import BusinessCard  # noqa: E402


class CardList(TableScreen):
    module_key = "frontdesk"
    model = BusinessCard
    tabs_field = "category"
    tabs_choices = BusinessCard.CATEGORIES
    search_fields = ["name", "email", "phone", "company", "job_title", "notes"]
    sort_fields = ["name", "company", "created_at"]
    default_sort = "name"
    title = "Business Cards"
    subtitle = "Cards handed to the desk: name, e-mail, number and what kind of contact they are. These can be used as an audience for bulk SMS and e-mail."
    placeholder = "Search name, company, e-mail, number…"
    export_name = "business-cards"
    empty_title = "No cards yet"
    empty_text = "Press New card to log the first one."
    columns = [
        Col("Name", "name", sort="name", link=True, sub="job_title"),
        Col("Company", "company", sort="company"),
        Col("Category", "get_category_display", kind="pill", tone=lambda o: {"supplier": "info", "tour_operator": "warning", "client": "success", "corporate": "info", "media": "neutral"}.get(o.category, "neutral")),
        Col("Phone", "phone", hide_phone=False),
        Col("E-mail", "email"),
        Col("Added", "created_at", sort="created_at", kind="date"),
    ]

    def actions(self):
        return [{"label": "New card", "url": reverse("frontdesk:card_new"), "style": "accent", "icon": "plus", "modal": "New business card"}] if self.request.user.can_run_desk else []

    def kpis(self, qs):
        return [{"label": "Cards", "value": qs.count(), "accent": True}, {"label": "With e-mail", "value": qs.exclude(email="").count()},
                {"label": "With number", "value": qs.exclude(phone="").count()}, {"label": "Added this month", "value": qs.filter(created_at__gte=timezone.now().replace(day=1, hour=0, minute=0)).count()}]


class CardCreate(CreateScreen):
    module_key = "frontdesk"
    edit_permission = "can_run_desk"
    model = BusinessCard
    form_class = BusinessCardForm
    title = "New business card"
    cancel_url_name = "frontdesk:cards"
    success_message = "Card saved."

    def form_valid(self, form):
        form.instance.added_by = self.request.user
        return super().form_valid(form)

    def get_success_url(self):
        return reverse("frontdesk:cards")


class CardUpdate(UpdateScreen):
    module_key = "frontdesk"
    edit_permission = "can_run_desk"
    model = BusinessCard
    form_class = BusinessCardForm
    cancel_url_name = "frontdesk:cards"
    success_message = "Card updated."

    def get_success_url(self):
        return reverse("frontdesk:cards")


@require_POST
@module_required("frontdesk")
def card_delete(request, pk):
    _require_desk(request.user)
    c = get_object_or_404(BusinessCard, pk=pk)
    c.delete()
    messages.success(request, f"{c.name} removed.")
    return redirect("frontdesk:cards")
