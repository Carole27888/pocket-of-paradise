from datetime import timedelta

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.permissions import module_required
from apps.core.crud import CreateScreen, UpdateScreen
from apps.core.tables import Col, TableScreen

from . import services
from .forms import BreachForm, DataRequestForm
from .models import DataBreach, DataRequest, RetentionRun

MOD = "compliance"
TONE = {"open": "warning", "in_progress": "info", "closed": "success"}


def _need(user):
    if not user.can_manage_compliance:
        raise PermissionDenied


@module_required(MOD)
def dashboard(request):
    today = timezone.localdate()
    open_ = DataRequest.objects.exclude(status="closed")
    return render(request, "compliance/dashboard.html", {
        "open": open_.order_by("due_on")[:8], "open_count": open_.count(), "overdue": sum(1 for r in open_ if r.is_overdue),
        "due_soon": open_.filter(due_on__range=(today, today + timedelta(days=7))).count(),
        "closed_month": DataRequest.objects.filter(status="closed", closed_at__date__gte=today.replace(day=1)).count(),
        "breaches": DataBreach.objects.all()[:5], "open_breaches": [b for b in DataBreach.objects.all() if b.status != "Closed"],
        "last_run": RetentionRun.objects.first(), "can_manage": request.user.can_manage_compliance,
        "anonymised": DataRequest.objects.filter(anonymised_at__isnull=False).count()})


class RequestList(TableScreen):
    module_key = MOD
    model = DataRequest
    tabs_field = "status"
    tabs_choices = DataRequest.STATUSES
    search_fields = ["reference", "requester_name", "requester_email", "details"]
    sort_fields = ["due_on", "received_on"]
    default_sort = "due_on"
    title = "Data requests"
    subtitle = "Access, correction, deletion and objection requests, answered within 30 days."
    placeholder = "Search reference, name, e-mail…"
    export_name = "data-requests"
    columns = [
        Col("Request", "reference", link=True, sub="requester_name"),
        Col("Type", "get_kind_display"),
        Col("Received", "received_on", kind="date"),
        Col("Due", "due_on", sort="due_on", kind="date", sub="days_label"),
        Col("Identity", "identity_label", kind="pill", tone=lambda o: "success" if o.identity_confirmed else "warning"),
        Col("Status", "status_label", kind="pill", tone=lambda o: "danger" if o.is_overdue else TONE[o.status]),
    ]

    def get_base_queryset(self):
        qs = DataRequest.objects.select_related("client")
        self._unfiltered = qs
        v = self.request.GET.get("status", "")
        return qs.filter(status=v) if v else qs

    def actions(self):
        return [{"label": "Log a request", "url": reverse("compliance:request_new"), "style": "accent", "icon": "plus"}] if self.request.user.can_manage_compliance else []

    def kpis(self, qs):
        o = qs.exclude(status="closed")
        return [{"label": "Open", "value": o.count(), "accent": True}, {"label": "Overdue", "value": sum(1 for r in o if r.is_overdue), "danger": True},
                {"label": "Closed this month", "value": qs.filter(status="closed", closed_at__date__gte=timezone.localdate().replace(day=1)).count()},
                {"label": "Erasures done", "value": qs.filter(anonymised_at__isnull=False).count()}]


DataRequest.identity_label = property(lambda self: "Confirmed" if self.identity_confirmed else "Not confirmed")
DataRequest.status_label = property(lambda self: "Overdue" if self.is_overdue else self.get_status_display())
DataRequest.days_label = property(lambda self: "" if self.status == "closed" else (f"{self.days_left} days left" if self.days_left >= 0 else f"{-self.days_left} days late"))


class RequestCreate(CreateScreen):
    module_key = MOD
    edit_permission = "can_manage_compliance"
    model = DataRequest
    form_class = DataRequestForm
    title = "Log a data request"
    cancel_url_name = "compliance:requests"

    def form_valid(self, form):
        resp = super().form_valid(form)
        from apps.system.services import audit

        audit(self.request.user, "dsr.logged", self.object, self.object.get_kind_display(), self.request)
        return resp


@module_required(MOD)
def request_detail(request, pk):
    r = get_object_or_404(DataRequest.objects.select_related("client"), pk=pk)
    return render(request, "compliance/request.html", {"r": r, "can_manage": request.user.can_manage_compliance,
                                                       "can_anonymise": r.kind == "deletion" and r.client_id and r.identity_confirmed and not r.anonymised_at})


@require_POST
@module_required(MOD)
def request_action(request, pk):
    _need(request.user)
    r = get_object_or_404(DataRequest, pk=pk)
    a = request.POST.get("action")
    from apps.system.services import audit

    try:
        if a == "identity":
            r.identity_confirmed, r.identity_note = True, request.POST.get("note", "")[:200]
            r.save(update_fields=["identity_confirmed", "identity_note", "updated_at"])
            audit(request.user, "dsr.identity_confirmed", r, r.identity_note, request)
            messages.success(request, "Identity recorded as confirmed.")
        elif a == "progress" and r.status == "open":
            r.status = "in_progress"
            r.save(update_fields=["status", "updated_at"])
        elif a == "link":
            from apps.contacts.models import Client

            c = Client.objects.alive().filter(pk=request.POST.get("client") or 0).first()
            if c is None:
                raise ValueError("Choose a client record.")
            r.client = c
            r.save(update_fields=["client", "updated_at"])
            messages.success(request, f"Linked to {c.full_name}.")
        elif a == "close":
            services.close_request(r, request.POST.get("response", ""), request.user)
            messages.success(request, "Request closed with your response on record.")
        elif a == "anonymise":
            counts = services.anonymise(r, request.user, request)
            messages.success(request, "Personal data scrubbed (financial records kept): " + ", ".join(f"{k} {v}" for k, v in counts.items() if k != "was"))
    except ValueError as exc:
        messages.error(request, str(exc))
    return redirect(r)


class BreachList(TableScreen):
    module_key = MOD
    model = DataBreach
    search_fields = ["reference", "title", "what_happened", "data_affected"]
    sort_fields = ["detected_at", "severity"]
    default_sort = "-detected_at"
    title = "Breach register"
    subtitle = "What happened, what data, and when it was detected, contained, reported and notified."
    export_name = "breaches"
    columns = [
        Col("Breach", "reference", link=True, sub="title"),
        Col("Detected", "detected_at", sort="detected_at", kind="datetime"),
        Col("People", "people_affected", kind="num"),
        Col("Severity", "get_severity_display", kind="pill", tone=lambda o: {"low": "neutral", "medium": "warning", "high": "danger"}[o.severity]),
        Col("Reported (ODPC)", "reported_label", kind="pill", tone=lambda o: "success" if o.reported_at else ("danger" if o.report_overdue else "warning")),
        Col("Status", "status", kind="pill", tone=lambda o: "success" if o.status == "Closed" else "warning"),
    ]

    def actions(self):
        return [{"label": "Record a breach", "url": reverse("compliance:breach_new"), "style": "accent", "icon": "plus"}] if self.request.user.can_manage_compliance else []

    def kpis(self, qs):
        return [{"label": "Breaches", "value": qs.count(), "accent": True}, {"label": "Not yet closed", "value": sum(1 for b in qs if b.status != "Closed")},
                {"label": "Report overdue (72h)", "value": sum(1 for b in qs if b.report_overdue), "danger": True}]


DataBreach.reported_label = property(lambda self: "Reported" if self.reported_at else ("Overdue" if self.report_overdue else "Not reported"))


class BreachCreate(CreateScreen):
    module_key = MOD
    edit_permission = "can_manage_compliance"
    model = DataBreach
    form_class = BreachForm
    title = "Record a data breach"
    cancel_url_name = "compliance:breaches"

    def form_valid(self, form):
        resp = super().form_valid(form)
        from apps.system.services import audit

        audit(self.request.user, "breach.recorded", self.object, f"{self.object.get_severity_display()} · {self.object.people_affected} people", self.request)
        if self.object.severity != "low":
            from apps.accounts import roles
            from apps.notifications.services import notify_roles

            notify_roles([roles.ADMIN, roles.COO, roles.DIRECTOR], f"Data breach recorded: {self.object.title}",
                         "Report to the regulator within 72 hours.", self.object.get_absolute_url(), "danger", dedupe_key=f"breach-new:{self.object.pk}")
        return resp


class BreachUpdate(UpdateScreen):
    module_key = MOD
    edit_permission = "can_manage_compliance"
    model = DataBreach
    form_class = BreachForm


@module_required(MOD)
def breach_detail(request, pk):
    b = get_object_or_404(DataBreach, pk=pk)
    return render(request, "compliance/breach.html", {"b": b, "can_manage": request.user.can_manage_compliance})


@module_required(MOD)
def retention(request):
    if request.method == "POST":
        if not request.user.has_role("Admin") and not request.user.can_manage_compliance:
            raise PermissionDenied
        counts = services.run_retention(request.user)
        messages.success(request, "Retention run complete: " + ", ".join(f"{k} {v}" for k, v in counts.items()))
        return redirect("compliance:retention")
    return render(request, "compliance/retention.html", {"rules": services.RETENTION.values(), "runs": RetentionRun.objects.all()[:10],
                                                         "can_manage": request.user.can_manage_compliance})


def privacy_notice(request):
    """Public privacy notice (no login)."""
    from apps.core.models import CompanySetting

    return render(request, "compliance/privacy_public.html", {"company": CompanySetting.load()})
