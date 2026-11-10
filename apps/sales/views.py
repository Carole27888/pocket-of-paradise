import csv
import json
from urllib.parse import quote
from datetime import timedelta
from decimal import Decimal

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.template.loader import render_to_string
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts import roles
from apps.accounts.permissions import is_scoped_agent, module_required
from apps.core.lists import ListScreen
from apps.leads import services as lead_services
from apps.leads.models import source_choices, source_codes, OPEN_STAGES, Enquiry, LeadActivity, Source, Stage
from apps.people.models import Employee

from . import analytics, services
from .filters import BookingFilter, CallFilter, FollowUpFilter, QuotationFilter
from .forms import (HotelFormSet, TravelFormSet, PriceLineFormSet, TemplatePriceFormSet, TemplateDayFormSet, TemplateForm, ItineraryDayFormSet, ItineraryForm, ItineraryStartForm, AcceptForm, AssignmentRuleForm, BookingEditForm, BookingStatusForm, CallForm, CallEditForm, DecisionForm,
                    FollowUpForm, PaymentForm, QuotationForm, QuotationItemFormSet, ReassignForm, SalesSettingsForm,
                    SendForm, TargetForm)
from .models import (AssignmentRule, Booking, BookingStatus, CallLog, FollowUp, LeadItinerary, Quotation,
                     QuotationStatus, SalesSettings, SalesTarget)

SALES = "sales"


# ------------------------------------------------------------------------------------------------ helpers
def _toast(resp, text, level="success"):
    resp["HX-Trigger"] = json.dumps({"toast": {"text": text, "level": level}})
    return resp


def _need_manager(user):
    if not user.can_manage_sales:
        raise PermissionDenied


def _need_team_view(user):
    if not user.sees_all_sales:
        raise PermissionDenied


def _need_work(user):
    if not user.can_work_sales:
        raise PermissionDenied


def _own_enquiry(request, pk, *, act=True):
    """An enquiry the user may see (and, for actions, work on)."""
    enquiry = get_object_or_404(
        services.visible_enquiries(request.user).select_related("client", "assigned_agent", "package"), pk=pk)
    if act and not services.can_act_on(request.user, enquiry):
        raise PermissionDenied
    return enquiry


def _actionable_enquiries(user):
    qs = services.visible_enquiries(user).filter(stage__in=OPEN_STAGES + [Stage.CONFIRMED, Stage.DEPOSIT])
    return qs.select_related("client", "package", "destination").order_by("-created_at")


def _team(request):
    """Agents whose numbers the user may see: everyone for managers/directors, only themselves for agents."""
    if request.user.sees_all_sales:
        return list(Employee.objects.filter(is_sales_agent=True, is_active=True))
    me = request.user.sales_agent_profile
    return [me] if me else []


def _range(request):
    return analytics.period(request.GET.get("range", "month"))


def _ctx(request, **kw):
    start, end, key = _range(request)
    return {"ranges": analytics.RANGES, "range_key": key, "start": start, "end": end, **kw}


def _redirect_back(request, default):
    nxt = request.POST.get("next") or request.GET.get("next") or ""
    return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else default)


# ------------------------------------------------------------------------------------------------ dashboard
@module_required(SALES)
def dashboard(request):
    ctx = _ctx(request)
    start, end = ctx["start"], ctx["end"]
    user = request.user
    team = _team(request)
    rows = analytics.agent_rows(start, end, team)
    tot = analytics.totals(rows)
    enq = services.visible_enquiries(user)
    open_qs = enq.filter(stage__in=OPEN_STAGES)
    now = timezone.now()
    fus = services.followups_for(user).filter(done_at__isnull=True)
    end_today = timezone.localtime().replace(hour=23, minute=59, second=59)
    tasks = fus.filter(due_at__lte=end_today).order_by("due_at")
    me_row = rows[0] if rows and not user.sees_all_sales else None
    ctx.update(
        scoped=is_scoped_agent(user), rows=sorted(rows, key=lambda r: -r["revenue"]), totals=tot,
        stages=analytics.stage_summary(enq), me=me_row,
        unassigned=open_qs.filter(assigned_agent__isnull=True).count(),
        breaches=services.sla_breaches(open_qs).select_related("client", "assigned_agent")[:6],
        breach_count=services.sla_breaches(open_qs).count(),
        cold=services.going_cold(open_qs).select_related("client", "assigned_agent").order_by("last_contact_at")[:6],
        tasks=tasks.select_related("enquiry__client", "agent")[:8], task_count=tasks.count(),
        overdue_count=fus.filter(due_at__lt=now).count(),
        pending_deposits=services.bookings_for(user).filter(status=BookingStatus.PENDING)[:5],
        recent_activity=(LeadActivity.objects.filter(enquiry__in=enq).select_related("enquiry__client", "user")
                         .order_by("-timestamp")[:10] if user.sees_all_sales else []),
        charts={"revenue": analytics.monthly_revenue(6, [a.pk for a in team] if team else []),
                "sources": analytics.source_mix(enq), "weekly": analytics.weekly_leads(enq, 10)},
        sla_minutes=SalesSettings.get().response_sla_minutes,
    )
    return render(request, "sales/dashboard.html", ctx)


# ------------------------------------------------------------------------------------------------ automation
@module_required(SALES)
def automation_page(request):
    """Sales → Automation: which follow-ups the system creates by itself, how soon, and what it has created lately."""
    from django.db.models import Count

    from . import automation
    from .forms import AutomationForm

    _need_manager(request.user)
    cfg = SalesSettings.get()
    form = AutomationForm(request.POST or None, instance=cfg)
    if request.method == "POST":
        if request.POST.get("action") == "run":
            made = automation.run_all(cfg)
            total = sum(made.values())
            messages.success(request, f"Ran every rule: {total} new follow-up{'s' if total != 1 else ''} created." if cfg.automation_on else "Automation is switched off.")
            return redirect("sales:automation")
        if form.is_valid():
            form.save()
            messages.success(request, "Automation settings saved.")
            return redirect("sales:automation")
    week = timezone.now() - timedelta(days=7)
    recent = FollowUp.objects.exclude(auto_key="").filter(created_at__gte=week)
    by_rule = {}
    for key in (r[0] for r in automation.RULES):
        by_rule[key] = recent.filter(auto_key__startswith=key.replace("quote_chase", "quote-chase")).count()
    by_rule["call-next"] = recent.filter(auto_key__startswith="call-next").count()
    rules = [{"key": k, "title": t, "text": d, "field": form[f], "unit": u, "made": by_rule.get(k, 0)} for k, t, d, f, u in automation.RULES]
    open_auto = FollowUp.objects.exclude(auto_key="").filter(done_at__isnull=True)
    return render(request, "sales/automation.html", {
        "form": form, "rules": rules, "made_week": recent.count(), "open_auto": open_auto.count(), "overdue_auto": open_auto.filter(due_at__lt=timezone.now()).count(),
        "done_auto": FollowUp.objects.exclude(auto_key="").filter(done_at__gte=week).count(), "call_next": by_rule["call-next"]})


# ------------------------------------------------------------------------------------------------ pipeline board
@module_required(SALES)
def board(request):
    user = request.user
    qs = services.visible_enquiries(user).filter(stage__in=services.BOARD_STAGES).select_related(
        "client", "package", "destination", "assigned_agent")
    agent_id = request.GET.get("agent", "")
    source = request.GET.get("source", "")
    term = request.GET.get("q", "").strip()
    if agent_id == "none":
        qs = qs.filter(assigned_agent__isnull=True)
    elif agent_id.isdigit() and user.sees_all_sales:
        qs = qs.filter(assigned_agent_id=int(agent_id))
    if source in source_codes():
        qs = qs.filter(source=source)
    if term:
        qs = qs.filter(Q(client__full_name__icontains=term) | Q(reference__icontains=term)
                       | Q(destination_text__icontains=term) | Q(package__title__icontains=term))
    # Settled stages only show the last 60 days so the board stays readable.
    recent = timezone.now() - timedelta(days=60)
    qs = qs.exclude(stage=Stage.CONFIRMED, stage_changed_at__lt=recent)
    cards = {s.value: [] for s in services.BOARD_STAGES}
    for e in qs.order_by("-stage_changed_at")[:600]:
        cards[e.stage].append(e)
    columns = []
    for s in services.BOARD_STAGES:
        items = cards[s.value]
        columns.append({"code": s.value, "label": s.label, "count": len(items), "cards": items[:40],
                        "more": max(0, len(items) - 40), "value": sum((e.deal_value for e in items), Decimal("0")),
                        "droppable": s in services.DROPPABLE, "weight": services.STAGE_WEIGHT.get(s.value, 0)})
    now = timezone.now()
    return render(request, "sales/board.html", {
        "columns": columns, "agents": Employee.objects.filter(is_sales_agent=True, is_active=True),
        "sources": source_choices(), "agent_id": agent_id, "source": source, "q": term,
        "can_move": user.can_work_sales, "now": now,
        "total_value": sum(c["value"] for c in columns),
        "weighted": sum(c["value"] * c["weight"] / 100 for c in columns),
    })


@require_POST
@module_required(SALES)
def board_move(request, pk):
    enquiry = _own_enquiry(request, pk)
    stage = request.POST.get("stage", "")
    reason = request.POST.get("reason", "").strip()
    try:
        target = Stage(stage)
    except ValueError:
        return JsonResponse({"ok": False, "error": "Unknown stage."}, status=400)
    if target not in services.DROPPABLE:
        return JsonResponse({"ok": False, "error": "Record a payment on the booking to move a deal there."},
                            status=400)
    if enquiry.stage in (Stage.DEPOSIT, Stage.CONFIRMED, Stage.COMPLETED) and hasattr(enquiry, "booking"):
        return JsonResponse({"ok": False, "error": "This deal has a paid booking; change it on the booking."},
                            status=400)
    if services.quote_needed(enquiry, target):
        return JsonResponse({"ok": False, "error": f"{enquiry.client.full_name} has no quotation yet.", **services.quote_prompt(enquiry, target)}, status=409)
    try:
        lead_services.change_stage(enquiry, target, request.user, reason)
    except ValueError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    return JsonResponse({"ok": True, "stage": target.value, "label": target.label})


# ------------------------------------------------------------------------------------------------ calls
class CallList(ListScreen):
    module_key = SALES
    model = CallLog
    filterset_class = CallFilter
    search_fields = ["enquiry__reference", "enquiry__client__full_name", "enquiry__client__phone", "notes"]
    sort_fields = ["called_at", "duration_seconds", "outcome"]
    default_sort = "-called_at"
    template_name = "sales/call_list.html"
    partial_template = "sales/partials/call_table.html"
    title = "Calls"
    subtitle = "Every call logged against a lead."
    export_name = "calls"
    export_columns = [("When", "called_at"), ("Enquiry", "enquiry.reference"), ("Client", "enquiry.client.full_name"),
                      ("Agent", "agent"), ("Direction", "get_direction_display"), ("Outcome", "get_outcome_display"),
                      ("Seconds", "duration_seconds"), ("Notes", "notes")]

    def get_base_queryset(self):
        return services.calls_for(self.request.user)

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        base = services.calls_for(self.request.user)
        today = base.filter(called_at__date=timezone.localdate())
        ctx["kpis"] = {"today": today.count(), "connected_today": today.filter(outcome__in=CallLog.CONNECTED).count(),
                       "week": base.filter(called_at__gte=timezone.now() - timedelta(days=7)).count(),
                       "minutes_week": sum(c.duration_seconds for c in base.filter(
                           called_at__gte=timezone.now() - timedelta(days=7))) // 60}
        ctx["scoped"] = is_scoped_agent(self.request.user)
        from django.db.models.functions import ExtractHour

        week = base.filter(called_at__gte=timezone.now() - timedelta(days=30))
        tz = timezone.get_current_timezone()
        hours = dict(week.annotate(h=ExtractHour("called_at", tzinfo=tz)).values_list("h").annotate(n=Count("pk")))
        outcomes = dict(week.values_list("outcome").annotate(n=Count("pk")))
        labels = dict(CallLog.OUTCOMES)
        by_agent = list(week.exclude(agent__isnull=True).values("agent__full_name").annotate(
            n=Count("pk"), ok=Count("pk", filter=Q(outcome__in=CallLog.CONNECTED))).order_by("-n")[:8])
        ctx["by_agent"] = [{"name": a["agent__full_name"], "calls": a["n"], "answered": round(100 * a["ok"] / a["n"]) if a["n"] else 0} for a in by_agent]
        ctx["charts"] = {"hours": {"labels": [f"{h % 12 or 12}{'a' if h < 12 else 'p'}" for h in range(7, 20)], "values": [hours.get(h, 0) for h in range(7, 20)]},
                         "outcomes": {"labels": [labels[k] for k in outcomes], "values": list(outcomes.values())}}
        ctx["answer_rate"] = round(100 * sum(n for k, n in outcomes.items() if k in CallLog.CONNECTED) / sum(outcomes.values())) if outcomes else 0
        return ctx


@module_required(SALES)
def call_new(request):
    _need_work(request.user)
    preset = None
    if request.GET.get("enquiry", request.POST.get("enquiry", "")).isdigit():
        preset = _own_enquiry(request, int(request.GET.get("enquiry") or request.POST.get("enquiry")))
    is_modal = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    form = CallForm(request.POST or None, enquiries=_actionable_enquiries(request.user), preset=preset)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        enquiry = d["enquiry"]
        if not services.can_act_on(request.user, enquiry):
            raise PermissionDenied
        services.log_call(enquiry, request.user, outcome=d["outcome"], direction=d["direction"],
                          duration_seconds=(d["minutes"] or 0) * 60, notes=d["notes"], called_at=d["called_at"],
                          follow_up_at=d["follow_up_at"], follow_up_title=d["follow_up_title"])
        messages.success(request, f"Call logged on {enquiry.reference}.")
        if is_modal:
            return JsonResponse({"ok": True})
        return _redirect_back(request, enquiry.get_absolute_url() if preset else reverse("sales:calls"))
    if is_modal:   # the pop-up loads and re-submits just the form; errors come back in the same fragment
        return render(request, "sales/partials/call_modal.html", {"form": form, "preset": preset}, status=422 if request.method == "POST" else 200)
    return render(request, "components/form_page.html", {
        "form": form, "title": "Log a call", "subtitle": preset and f"{preset.reference} · {preset.client.full_name}",
        "cancel_url": preset.get_absolute_url() if preset else reverse("sales:calls")})


@module_required(SALES)
def call_edit(request, pk):
    """Edit a logged call (mostly its notes). Agents change their own calls, managers any."""
    from django.utils import timezone as _tz

    call = get_object_or_404(services.calls_for(request.user), pk=pk)
    if not services.can_act_on(request.user, call.enquiry):
        raise PermissionDenied
    is_modal = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    form = CallEditForm(request.POST or None, initial={
        "direction": call.direction, "outcome": call.outcome, "minutes": call.duration_seconds // 60,
        "called_at": _tz.localtime(call.called_at), "notes": call.notes})
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        call.direction, call.outcome, call.notes, call.called_at = d["direction"], d["outcome"], d["notes"], d["called_at"]
        if d["minutes"] is not None:
            call.duration_seconds = d["minutes"] * 60
        call.save()
        lead_services.log_activity(call.enquiry, "note", f"Call record edited ({call.get_outcome_display()})", request.user)
        messages.success(request, "Call updated.")
        if is_modal:
            return JsonResponse({"ok": True})
        return _redirect_back(request, reverse("sales:calls"))
    if is_modal:   # edits open in a pop-up: the form is fetched as a fragment and re-rendered here if it has errors
        return render(request, "components/form_modal.html", {
            "form": form, "action": request.path, "save_label": "Save call",
            "modal_sub": f"{call.enquiry.reference} · {call.enquiry.client.full_name}"}, status=422 if request.method == "POST" else 200)
    return render(request, "components/form_page.html", {
        "form": form, "title": "Edit call", "subtitle": f"{call.enquiry.reference} · {call.enquiry.client.full_name}",
        "cancel_url": reverse("sales:calls")})


# ------------------------------------------------------------------------------------------------ follow-ups
@module_required(SALES)
def followups(request):
    user = request.user
    qs = services.followups_for(user)
    flt = FollowUpFilter(request.GET, queryset=qs)
    qs = flt.qs
    now = timezone.now()
    end_today = timezone.localtime().replace(hour=23, minute=59, second=59)
    open_ = qs.filter(done_at__isnull=True)
    return render(request, "sales/followups.html", {
        "filter": flt, "overdue": open_.filter(due_at__lt=now), "today": open_.filter(due_at__gte=now, due_at__lte=end_today),
        "upcoming": open_.filter(due_at__gt=end_today)[:40],
        "done": qs.filter(done_at__isnull=False).order_by("-done_at")[:10], "scoped": is_scoped_agent(user),
        "title": "Follow-ups"})


@module_required(SALES)
def followup_new(request):
    _need_work(request.user)
    preset = None
    raw = request.GET.get("enquiry", request.POST.get("enquiry", ""))
    if raw.isdigit():
        preset = _own_enquiry(request, int(raw))
    form = FollowUpForm(request.POST or None, enquiries=_actionable_enquiries(request.user), preset=preset)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        if not services.can_act_on(request.user, d["enquiry"]):
            raise PermissionDenied
        services.add_followup(d["enquiry"], request.user, kind=d["kind"], title=d["title"], due_at=d["due_at"])
        messages.success(request, "Follow-up scheduled.")
        return _redirect_back(request, d["enquiry"].get_absolute_url() if preset else reverse("sales:followups"))
    return render(request, "components/form_page.html", {
        "form": form, "title": "New follow-up", "cancel_url": preset.get_absolute_url() if preset else reverse("sales:followups")})


@require_POST
@module_required(SALES)
def followup_done(request, pk):
    fu = get_object_or_404(services.followups_for(request.user), pk=pk)
    if not services.can_act_on(request.user, fu.enquiry):
        raise PermissionDenied
    services.complete_followup(fu, request.user)
    if request.htmx:
        return _toast(HttpResponse(""), "Marked done.")
    return _redirect_back(request, reverse("sales:followups"))


# ------------------------------------------------------------------------------------------------ quotations
class QuotationList(ListScreen):
    module_key = SALES
    model = Quotation
    filterset_class = QuotationFilter
    search_fields = ["reference", "title", "enquiry__reference", "enquiry__client__full_name"]
    sort_fields = ["created_at", "total", "status", "sent_at"]
    default_sort = "-created_at"
    template_name = "sales/quotation_list.html"
    partial_template = "sales/partials/quotation_table.html"
    title = "Quotations"
    subtitle = "Versioned price offers. Revising a sent quotation creates a new version."
    export_name = "quotations"
    export_columns = [("Quotation", "reference"), ("Version", "version"), ("Client", "enquiry.client.full_name"),
                      ("Enquiry", "enquiry.reference"), ("Status", "get_status_display"), ("Currency", "currency"),
                      ("Total", "total"), ("Sent", "sent_at"), ("Valid until", "valid_until")]

    def get_base_queryset(self):
        return services.quotations_for(self.request.user)

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        base = services.quotations_for(self.request.user)
        sent = base.filter(status=QuotationStatus.SENT)
        ctx["kpis"] = {
            "draft": base.filter(status=QuotationStatus.DRAFT).count(), "sent": sent.count(),
            "sent_value": sum((q.total_kes for q in sent), Decimal("0")),
            "accepted": base.filter(status=QuotationStatus.ACCEPTED).count(),
            "expiring": sent.filter(valid_until__lte=timezone.localdate() + timedelta(days=3)).count(),
        }
        return ctx


def _quotation(request, pk, *, act=False):
    q = get_object_or_404(services.quotations_for(request.user), pk=pk)
    if act and not services.can_act_on(request.user, q.enquiry):
        raise PermissionDenied
    return q


@module_required(SALES)
def quotation_new(request):
    enquiry = _own_enquiry(request, int(request.GET.get("enquiry", 0) or 0)) if request.GET.get("enquiry", "").isdigit() else None
    if enquiry is None:
        raise Http404("Choose an enquiry to quote from its page.")
    # a quotation is always made from an itinerary: no itinerary yet means the agent builds one first
    its = list(enquiry.itineraries.order_by("-created_at"))
    if not its:
        messages.warning(request, f"Build {enquiry.client.full_name}'s itinerary first. A quotation is made from it.")
        return redirect(reverse("sales:itinerary_new") + f"?enquiry={enquiry.pk}")
    source = next((i for i in its if i.quotation_id is None), its[0])       # the newest itinerary that has not been quoted yet
    try:
        q = services.itinerary_to_quotation(source, request.user)
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("sales:itinerary_edit", pk=source.pk)
    messages.success(request, f"Draft {q.reference} created from the itinerary “{source.title}”. Check the lines, then send it.")
    return redirect("sales:quotation_edit", pk=q.pk)


@module_required(SALES)
def quotation_detail(request, pk):
    q = _quotation(request, pk)
    can_act = services.can_act_on(request.user, q.enquiry)
    return render(request, "sales/quotation_detail.html", {
        "q": q, "items": q.items.all(), "can_act": can_act, "send_form": SendForm(), "decision_form": DecisionForm(),
        "accept_form": AcceptForm(initial={"travel_start": q.enquiry.travel_date_from,
                                           "travel_end": q.enquiry.travel_date_to}),
        "versions": q.enquiry.quotations.order_by("-version"), "wa_text": _wa_text(request, q),
        "itinerary": q.itineraries.first() or q.enquiry.itineraries.exclude(quotation__isnull=False).first()})


def _wa_text(request, q):
    from urllib.parse import quote

    url = request.build_absolute_uri(reverse("sales:quotation_print", args=[q.pk]))
    return quote(f"Hello {q.enquiry.client.full_name.split()[0]}, here is your safari quotation {q.reference} "
                 f"({q.currency} {q.total:,.0f}). Valid until {q.valid_until or 'further notice'}.")


@module_required(SALES)
def quotation_edit(request, pk):
    q = _quotation(request, pk, act=True)
    if not q.is_editable:
        messages.warning(request, "Only drafts can be edited. Revise it to make a new version.")
        return redirect(q)
    form = QuotationForm(request.POST or None, instance=q)
    formset = QuotationItemFormSet(request.POST or None, instance=q)
    if request.method == "POST" and form.is_valid() and formset.is_valid():
        q = form.save()
        formset.save()
        for pos, item in enumerate(q.items.all()):
            if item.position != pos:
                item.position = pos
                item.save(update_fields=["position"])
        q.fx_rate = services.fx_rate(q.currency)
        q.save(update_fields=["fx_rate"])
        q.recalculate()
        messages.success(request, f"{q.reference} saved · total {q.currency} {q.total:,.2f}")
        return redirect(q)
    return render(request, "sales/quotation_form.html", {"q": q, "form": form, "formset": formset})


@require_POST
@module_required(SALES)
def quotation_send(request, pk):
    q = _quotation(request, pk, act=True)
    form = SendForm(request.POST)
    if not q.enquiry.itineraries.exists():
        messages.error(request, "A quotation needs an itinerary. Build the itinerary for this lead first.")
        return redirect(q)
    if form.is_valid():
        try:
            services.send_quotation(q, form.cleaned_data["via"], request.user)
            messages.success(request, f"{q.reference} marked as sent.")
        except ValueError as exc:
            messages.error(request, str(exc))
    return redirect(q)


@require_POST
@module_required(SALES)
def quotation_revise(request, pk):
    old = _quotation(request, pk, act=True)
    if old.status == QuotationStatus.DRAFT:
        return redirect("sales:quotation_edit", pk=old.pk)
    new = services.revise_quotation(old, request.user)
    messages.success(request, f"Version {new.version} created as a draft.")
    return redirect("sales:quotation_edit", pk=new.pk)


@require_POST
@module_required(SALES)
def quotation_reject(request, pk):
    q = _quotation(request, pk, act=True)
    try:
        services.reject_quotation(q, request.user, request.POST.get("note", ""))
        messages.success(request, "Marked as declined by the client.")
    except ValueError as exc:
        messages.error(request, str(exc))
    return redirect(q)


@require_POST
@module_required(SALES)
def quotation_accept(request, pk):
    q = _quotation(request, pk, act=True)
    form = AcceptForm(request.POST)
    if not form.is_valid():
        messages.error(request, "; ".join(e for errs in form.errors.values() for e in errs))
        return redirect(q)
    d = form.cleaned_data
    try:
        booking = services.accept_quotation(q, request.user, travel_start=d["travel_start"],
                                            travel_end=d["travel_end"], deposit_percent=d["deposit_percent"])
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect(q)
    messages.success(request, f"Booking {booking.reference} created. Record the deposit when it arrives.")
    return redirect(booking)


@module_required(SALES)
def quotation_print(request, pk):
    from apps.core.models import CompanySetting

    q = _quotation(request, pk)
    ctx = {"q": q, "items": q.items.all(), "company": CompanySetting.load(),
           "print": request.GET.get("print") == "1"}
    return render(request, "sales/quotation_print.html", ctx)


@module_required(SALES)
def quotation_pdf(request, pk):
    q = _quotation(request, pk)
    try:
        from weasyprint import HTML
    except Exception:  # WeasyPrint (and its system libraries) are optional; the print page is the fallback
        messages.info(request, "PDF engine not installed here; use Print → Save as PDF.")
        return redirect(f"{reverse('sales:quotation_print', args=[q.pk])}?print=1")
    from django.template.loader import render_to_string
    from apps.core.models import CompanySetting

    html = render_to_string("sales/quotation_print.html", {"q": q, "items": q.items.all(),
                                                           "company": CompanySetting.load()}, request)
    pdf = HTML(string=html, base_url=request.build_absolute_uri("/")).write_pdf()
    resp = HttpResponse(pdf, content_type="application/pdf")
    resp["Content-Disposition"] = f'inline; filename="{q.reference}-v{q.version}.pdf"'
    return resp


# ------------------------------------------------------------------------------------------------ itinerary builder
from apps.core.pdf import render_pdf  # noqa: E402
from apps.core.tables import Col, TableScreen  # noqa: E402


class ItineraryList(TableScreen):
    module_key = SALES
    model = LeadItinerary
    tabs_field = "status"
    tabs_choices = [("draft", "Draft"), ("sent", "Sent")]
    search_fields = ["title", "enquiry__reference", "enquiry__client__full_name"]
    sort_fields = ["created_at", "start_date"]
    default_sort = "-created_at"
    title = "Itineraries"
    subtitle = "Custom day-by-day itineraries priced from lodge rates. Turn one into a quotation in a click."
    placeholder = "Search itinerary, client, lead…"
    export_name = "itineraries"
    columns = [
        Col("Itinerary", "title", link=True, sub="enquiry"),
        Col("Client", "enquiry.client.full_name", sub="enquiry.assigned_agent"),
        Col("Travel", "start_date", sort="start_date", kind="date", sub="days_label"),
        Col("Sell price", "sell_total", kind="money", currency="currency"),
        Col("Status", "get_status_display", kind="pill", tone=lambda o: "success" if o.status == "sent" else "warning"),
        Col("Action", kind="action", export=False, action=lambda o, r: _delete_action(o, r)),
    ]

    def get_base_queryset(self):
        qs = services.itineraries_for(self.request.user).prefetch_related("days")
        self._unfiltered = qs
        v = self.request.GET.get("status", "")
        return qs.filter(status=v) if v else qs

    def kpis(self, qs):
        return [{"label": "Drafts", "value": qs.filter(status="draft").count(), "accent": True},
                {"label": "Sent to clients", "value": qs.filter(status="sent").count()},
                {"label": "Turned into quotes", "value": qs.filter(quotation__isnull=False).count()}]


def _can_delete_itinerary(user, it):
    """Your own drafts, or anyone's if you manage sales. A sent itinerary stays on record unless a manager removes it."""
    return services.can_act_on(user, it.enquiry) and (it.status == it.DRAFT or user.can_manage_sales)


def _delete_action(it, request):
    if request is None or not _can_delete_itinerary(request.user, it):
        return None
    return {"url": reverse("sales:itinerary_delete", args=[it.pk]), "label": "Delete",
            "confirm": f"Delete “{it.title}”? Its days, pictures and client link are removed. This can't be undone."}


@require_POST
@module_required(SALES)
def itinerary_delete(request, pk):
    it = _itinerary(request, pk)
    if not _can_delete_itinerary(request.user, it):
        raise PermissionDenied
    from apps.leads import services as lead_services

    title, enquiry = it.title, it.enquiry
    it.delete()
    lead_services.log_activity(enquiry, "note", f"Itinerary “{title}” deleted", request.user)
    messages.success(request, f"Itinerary “{title}” deleted.")
    return redirect("sales:itineraries")


def _itinerary(request, pk, *, act=False):
    it = get_object_or_404(services.itineraries_for(request.user), pk=pk)
    if act and not services.can_act_on(request.user, it.enquiry):
        raise PermissionDenied
    return it


@module_required(SALES)
def itinerary_new(request):
    """Build an itinerary for one of your leads. Opened from a lead it is preset; from the menu you pick the lead here."""
    _need_work(request.user)
    raw = request.GET.get("enquiry", request.POST.get("enquiry", ""))
    preset = _own_enquiry(request, int(raw)) if raw.isdigit() else None
    tpl = request.GET.get("template", "")
    pk_pkg = request.GET.get("package", "")
    # leads that already have a quotation are not offered here (they are listed under "Quoted itineraries"); a lead opened from its own page still works
    pickable = _actionable_enquiries(request.user).exclude(pk__in=Quotation.objects.values("enquiry_id"))
    if preset is not None:
        pickable = _actionable_enquiries(request.user).filter(Q(pk=preset.pk) | Q(pk__in=pickable.values("pk")))
    form = ItineraryStartForm(request.POST or None, enquiries=pickable, preset=preset,
                              initial={"package": int(pk_pkg) if pk_pkg.isdigit() else (preset.package_id if preset and not tpl else None), "template": int(tpl) if tpl.isdigit() else None})
    if request.method == "POST" and form.is_valid():
        enquiry = form.cleaned_data["enquiry"]
        if not services.can_act_on(request.user, enquiry):
            raise PermissionDenied
        it = services.create_itinerary(enquiry, request.user, package=form.cleaned_data["package"], title=form.cleaned_data["title"],
                                       template=form.cleaned_data["template"])
        messages.success(request, "Itinerary created. Add days, lodges and prices.")
        return redirect("sales:itinerary_edit", pk=it.pk)
    return render(request, "sales/itinerary_new.html", {
        "recent": services.itineraries_for(request.user).order_by("-created_at")[:8],
        "quoted": services.itineraries_for(request.user).filter(quotation__isnull=False).select_related("quotation").order_by("-quotation__created_at")[:8],
        "quoted_count": services.itineraries_for(request.user).filter(quotation__isnull=False).count(),
        "form": form, "title": "Build an itinerary",
        "subtitle": f"{preset.reference} · {preset.client.full_name} · {preset.party_label}" if preset else "Choose the lead you are building it for, then add days, lodges and prices.",
        "cancel_url": preset.get_absolute_url() if preset else reverse("sales:itineraries")})


@module_required(SALES)
def itinerary_detail(request, pk):
    it = _itinerary(request, pk)
    days = list(it.days.select_related("destination", "accommodation"))
    from apps.core.models import CompanySetting
    from urllib.parse import quote as _q

    from . import vouchers

    co = CompanySetting.load()
    lodge_requests = []
    for st in vouchers.stays(it):
        txt = vouchers.request_text(it, st, co)
        acc = st["lodge"]
        lodge_requests.append({"stay": st, "text": txt, "mailto": f"mailto:{acc.contact_email}?subject={_q('Booking request ' + it.enquiry.reference)}&body={_q(txt)}" if acc.contact_email else "",
                               "wa": f"{__import__('apps.core.phone', fromlist=['x']).normalise_phone(acc.contact_phone).lstrip('+')}?text={_q(txt)}" if acc.contact_phone else ""})
    return render(request, "sales/itinerary_detail.html", {
        "lodge_requests": lodge_requests,
        "it": it, "days": days, "responses": it.responses.select_related("hotel")[:10], "can_act": services.can_act_on(request.user, it.enquiry), "can_delete": _can_delete_itinerary(request.user, it), "send_form": SendForm(),
        "wa_text": _itinerary_wa(request, it), "breakdown": _breakdown(it, days),
        "share_url": request.build_absolute_uri(it.public_path()) if it.share_token else ""})


def _breakdown(it, days):
    pax = it.pax
    lodging = sum((d.lodging_pp * pax for d in days), Decimal("0"))
    acts = sum((d.activities_pp * pax for d in days), Decimal("0"))
    transport = sum((d.transport for d in days), Decimal("0"))
    rows = [("Lodging", lodging), ("Activities & fees", acts), ("Vehicle & guide", transport)]
    if it.lines_total:
        rows.append(("Extra lines (no margin)", it.lines_total))
    if it.hotel_from:
        rows.append(("Cheapest lodge option", it.hotel_from))
    if it.travel_from:
        rows.append(("Cheapest travel option", it.travel_from))
    return rows


def _itinerary_wa(request, it):
    from urllib.parse import quote

    first = it.enquiry.client.full_name.split()[0]
    link = f" See it here: {request.build_absolute_uri(it.public_path())} ." if it.share_token else ""
    return quote(f"Hello {first}, here is your proposed {it.day_count}-day safari itinerary: {it.title}. "
                 f"{'From ' + it.currency + ' ' + format(it.sell_pp, ',.0f') + ' per person. ' if it.sell_total else ''}{link}Tell us what you'd like to change!")


@module_required(SALES)
def itinerary_edit(request, pk):
    it = _itinerary(request, pk, act=True)
    form = ItineraryForm(request.POST or None, instance=it)
    formset = ItineraryDayFormSet(request.POST or None, request.FILES or None, instance=it)
    # the extra lines and lodge options are optional parts of the form: a post without them leaves them as they are
    lines_in = request.method == "POST" and "price_lines-TOTAL_FORMS" in request.POST
    hotels_in = request.method == "POST" and "hotels-TOTAL_FORMS" in request.POST
    travel_in = request.method == "POST" and "travel-TOTAL_FORMS" in request.POST
    lines = PriceLineFormSet(request.POST if lines_in else None, instance=it, prefix="price_lines")
    hotels = HotelFormSet(request.POST if hotels_in else None, instance=it, prefix="hotels")
    travel = TravelFormSet(request.POST if travel_in else None, instance=it, prefix="travel")
    if (request.method == "POST" and form.is_valid() and formset.is_valid() and (not lines_in or lines.is_valid())
            and (not hotels_in or hotels.is_valid()) and (not travel_in or travel.is_valid())):
        form.save()
        formset.save()
        if lines_in:
            lines.save()
        if hotels_in:
            hotels.save()
        if travel_in:
            travel.save()
        for pos, day in enumerate(it.days.order_by("day_number", "pk"), start=1):   # keep days numbered 1..n in order
            if day.day_number != pos:
                day.day_number = pos
                day.save(update_fields=["day_number"])
        floor = SalesSettings.get().min_margin_percent
        if it.margin_percent < floor:
            messages.warning(request, f"The margin ({it.margin_percent:g}%) is below the lowest allowed ({floor:g}%). A sales manager has to approve it before it can be sent.")
            from apps.notifications.services import notify_roles

            notify_roles(["Sales Manager"], "Low-margin itinerary needs approval", f"{it.title}: {it.margin_percent:g}% margin, floor {floor:g}%", it.get_absolute_url(), "warning",
                         dedupe_key=f"lowmargin:{it.pk}:{it.margin_percent}")
        messages.success(request, f"Itinerary saved · {it.currency} {it.grand_total:,.0f} total")
        if "save_pdf" in request.POST:
            return redirect("sales:itinerary_pdf", pk=it.pk)
        return redirect(it)
    from apps.catalog.models import Accommodation, PhotoCategory

    packs = [{"id": c.pk, "name": c.name} for c in PhotoCategory.objects.filter(kind="destination")]
    return render(request, "sales/itinerary_form.html", {
        "it": it, "form": form, "formset": formset, "lines": lines, "hotels": hotels, "travel": travel, "margin_floor": SalesSettings.get().min_margin_percent, "packs_json": json.dumps(packs), "write_url": reverse("sales:itinerary_write", args=[it.pk]), "pax": it.pax, "library": _library(),
        "rated": list(Accommodation.objects.alive().filter(rates__isnull=False).values_list("pk", flat=True).distinct())})


def _library(exclude=None):
    from .models import ItineraryTemplate

    qs = ItineraryTemplate.objects.prefetch_related("days__destination", "days__template").order_by("title")
    return qs.exclude(pk=exclude) if exclude else qs


# ------------------------------------------------------------------------------------------------ itinerary templates
def _can_edit_template(user, t):
    """The whole sales team keeps the shared library up to date, so anyone who works in sales may edit a template."""
    return user.can_work_sales


def _can_delete_template(user, t):
    return user.is_superuser or user.can_manage_sales or t.created_by_id == user.pk


@module_required(SALES)
def templates_list(request):
    from .models import ItineraryTemplate

    q = (request.GET.get("q") or "").strip()
    qs = ItineraryTemplate.objects.prefetch_related("days__destination").select_related("created_by")
    if q:
        qs = qs.filter(Q(title__icontains=q) | Q(description__icontains=q) | Q(days__title__icontains=q)).distinct()
    items = [{"t": t, "can_edit": _can_edit_template(request.user, t), "can_delete": _can_delete_template(request.user, t)} for t in qs]
    return render(request, "sales/template_list.html", {"items": items, "q": q, "can_work": request.user.can_work_sales})


@module_required(SALES)
def template_detail(request, pk):
    from .models import ItineraryTemplate

    t = get_object_or_404(ItineraryTemplate.objects.prefetch_related("days__destination", "days__accommodation").select_related("created_by"), pk=pk)
    return render(request, "sales/template_detail.html", {"t": t, "days": list(t.days.all()), "can_edit": _can_edit_template(request.user, t), "can_delete": _can_delete_template(request.user, t),
                                                          "can_work": request.user.can_work_sales, "used_in": t.times_used})


@module_required(SALES)
def template_new(request):
    from .models import ItineraryTemplate, TemplateDay

    _need_work(request.user)
    form = TemplateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        t = form.save(commit=False)
        t.created_by = request.user
        t.save()
        TemplateDay.objects.create(template=t, day_number=1, title="Arrival and transfer")
        messages.success(request, "Template created. Add days, or drag days in from other templates.")
        return redirect("sales:template_edit", pk=t.pk)
    return render(request, "components/form_page.html", {"form": form, "title": "New itinerary template", "subtitle": "Give it a name; you add the days next.",
                                                         "cancel_url": reverse("sales:templates")})


@module_required(SALES)
def template_edit(request, pk):
    from .models import ItineraryTemplate

    t = get_object_or_404(ItineraryTemplate, pk=pk)
    if not _can_edit_template(request.user, t):
        raise PermissionDenied
    form = TemplateForm(request.POST or None, instance=t)
    formset = TemplateDayFormSet(request.POST or None, request.FILES or None, instance=t)
    lines_in = request.method == "POST" and "price_lines-TOTAL_FORMS" in request.POST
    lines = TemplatePriceFormSet(request.POST if lines_in else None, instance=t, prefix="price_lines")
    if request.method == "POST" and form.is_valid() and formset.is_valid() and (not lines_in or lines.is_valid()):
        form.save()
        formset.save()
        if lines_in:
            lines.save()
        for pos, day in enumerate(t.days.order_by("day_number", "pk"), start=1):
            if day.day_number != pos:
                day.day_number = pos
                day.save(update_fields=["day_number"])
        messages.success(request, "Template saved.")
        return redirect("sales:templates")
    return render(request, "sales/template_form.html", {"t": t, "form": form, "formset": formset, "lines": lines, "library": _library(exclude=t.pk),
                                                        "rated": []})


@require_POST
@module_required(SALES)
def template_delete(request, pk):
    from .models import ItineraryTemplate

    t = get_object_or_404(ItineraryTemplate, pk=pk)
    if not _can_delete_template(request.user, t):
        raise PermissionDenied
    t.delete()
    messages.success(request, f"Template “{t.title}” deleted. Itineraries already built from it are not affected.")
    return redirect("sales:templates")


@require_POST
@module_required(SALES)
def template_duplicate(request, pk):
    from .models import ItineraryTemplate

    _need_work(request.user)
    n = services.duplicate_template(get_object_or_404(ItineraryTemplate, pk=pk), request.user)
    messages.success(request, "Copied. Edit the copy to make it your own.")
    return redirect("sales:template_edit", pk=n.pk)


@require_POST
@module_required(SALES)
def itinerary_save_template(request, pk):
    it = _itinerary(request, pk, act=True)
    t = services.template_from_itinerary(it, request.user, request.POST.get("title", "").strip()[:160])
    messages.success(request, f"Saved as template “{t.title}”. Find it under Itineraries → Templates.")
    return redirect("sales:template_edit", pk=t.pk)


@require_POST
@module_required(SALES)
def itinerary_write(request, pk):
    """JSON: the words for one day, or the welcome paragraph, written from the facts on the form."""
    from . import writer

    it = _itinerary(request, pk, act=True)
    if request.POST.get("what") == "intro":
        return JsonResponse(writer.write_intro(it))
    return JsonResponse(writer.write_day(request.POST))


@module_required(SALES)
def itinerary_rate(request):
    """JSON: suggested lodge rate for an accommodation on a date (used by the builder)."""
    from apps.catalog.models import Accommodation

    acc = Accommodation.objects.alive().filter(pk=request.GET.get("accommodation") or 0).first()
    if acc is None:
        return JsonResponse({"rate": None})
    try:
        on = timezone.datetime.fromisoformat(request.GET.get("date", "")).date()
    except ValueError:
        on = None
    cur = request.GET.get("currency", "USD") if request.GET.get("currency") in ("KES", "USD", "EUR", "GBP") else "USD"
    rate, room = services.suggest_rate(acc, on, cur, request.GET.get("room", ""))
    return JsonResponse({"rate": str(rate) if rate is not None else None, "room_type": room, "destination": acc.destination_id})


@module_required(SALES)
def itinerary_print(request, pk):
    """The client view in the browser: the very same design as the PDF (cover, glance, day cards, lodges, pricing, payment), ready to print or save as PDF."""
    from apps.core.models import CompanySetting

    from . import itinerary_pdf as slides

    it = _itinerary(request, pk)
    ctx = slides.build_context(it, CompanySetting.load(), web=True)
    if request.GET.get("prices") == "0":
        ctx.update(prices=[], options=[], hide_prices=True)
    ctx.update(print=request.GET.get("print") == "1", pdf_url=reverse("sales:itinerary_pdf", args=[it.pk]) + "?download=1")
    return render(request, "sales/itinerary_slides.html", ctx)


@module_required(SALES)
def itinerary_pdf(request, pk):
    """The client PDF: landscape slides with cover, photos per day, lodge options, pricing and payment details."""
    from apps.core.models import CompanySetting

    from . import itinerary_pdf as slides

    it = _itinerary(request, pk)
    try:
        data = slides.build_pdf(it, CompanySetting.load())
    except Exception:   # WeasyPrint or its system libraries are missing on this host
        messages.info(request, "PDF engine not available here; use Print → Save as PDF.")
        return redirect("sales:itinerary_print", pk=pk)
    resp = HttpResponse(data, content_type="application/pdf")
    kind = "attachment" if request.GET.get("download") else "inline"
    resp["Content-Disposition"] = f'{kind}; filename="itinerary-{it.enquiry.reference}-{it.pk}.pdf"'
    return resp


@module_required(SALES)
def itinerary_vouchers(request, pk):
    """One accommodation voucher per lodge stay, as a PDF to give the client or send to the lodge."""
    from pathlib import Path

    from django.conf import settings as dj_settings

    from apps.core.models import CompanySetting

    from . import vouchers

    it = _itinerary(request, pk)
    booking = getattr(it.enquiry, "booking", None)
    logo = Path(dj_settings.BASE_DIR) / "static" / "brand" / "logo-full.png"
    ctx = {"it": it, "stays": vouchers.stays(it), "company": CompanySetting.load(), "ref": booking.reference if booking else it.enquiry.reference,
           "logo": logo.as_uri() if logo.exists() else "", "today": timezone.localdate()}
    try:
        from weasyprint import HTML

        pdf = HTML(string=render_to_string("sales/vouchers.html", ctx), base_url=str(dj_settings.BASE_DIR)).write_pdf()
    except Exception:
        return render(request, "sales/vouchers.html", ctx)
    resp = HttpResponse(pdf, content_type="application/pdf")
    resp["Content-Disposition"] = f'inline; filename="vouchers-{ctx["ref"]}.pdf"'
    return resp


@require_POST
@module_required(SALES)
def itinerary_duplicate(request, pk):
    it = _itinerary(request, pk, act=True)
    n = services.duplicate_itinerary(it, request.user)
    messages.success(request, "Copied as a new draft. Change what you like, then send both options to the client.")
    return redirect("sales:itinerary_edit", pk=n.pk)


@require_POST
@module_required(SALES)
def itinerary_quote(request, pk):
    it = _itinerary(request, pk, act=True)
    try:
        q = services.itinerary_to_quotation(it, request.user)
        messages.success(request, f"Draft quotation {q.reference} created from this itinerary.")
        return redirect("sales:quotation_edit", pk=q.pk)
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect(it)


@require_POST
@module_required(SALES)
def itinerary_share(request, pk):
    it = _itinerary(request, pk, act=True)
    if request.POST.get("action") == "revoke":
        services.revoke_share(it, request.user)
        messages.success(request, "Client link switched off. The old address no longer works.")
    else:
        if not it.days.exists():
            messages.error(request, "Add at least one day before sharing.")
        else:
            services.enable_share(it, request.user)
            messages.success(request, "Client link ready. Copy it or send it on WhatsApp.")
    return redirect(it)


def trip_public(request, token):
    """Public itinerary page for the client (no login). The link is the secret, so it is not indexed or cached."""
    from apps.core.models import CompanySetting

    it = get_object_or_404(LeadItinerary.objects.select_related("enquiry__client", "enquiry__assigned_agent"), share_token=token)
    services.record_view(it)
    days = list(it.days.select_related("destination", "accommodation"))
    stays = []
    for d in days:
        if d.accommodation and d.accommodation.name not in [s["name"] for s in stays]:
            stays.append({"name": d.accommodation.name, "nights": sum(1 for x in days if x.accommodation_id == d.accommodation_id)})
    cover = next((reverse("public_trip_image", args=[token, d.pk]) for d in days if d.gallery_files), "")
    hotels = list(it.hotels.select_related("photo_pack"))
    for h in hotels:
        h.cover = h.photo_pack.cover if h.photo_pack_id else None
    resp = render(request, "sales/public_trip.html", {"it": it, "days": days, "stays": stays, "cover": cover, "company": CompanySetting.load(), "hotels": hotels,
                                                      "agent": it.enquiry.assigned_agent, "client_first": it.enquiry.client.full_name.split()[0]})
    resp["X-Robots-Tag"] = "noindex, nofollow"
    resp["Cache-Control"] = "private, no-store"
    return resp


def trip_image(request, token, day_id):
    """A day's picture for the public client page. Served by link token so private uploads stay behind login."""
    import mimetypes

    from django.http import FileResponse

    it = get_object_or_404(LeadItinerary, share_token=token)
    day = get_object_or_404(it.days.all(), pk=day_id)
    files = day.gallery_files
    n = int(request.GET["n"]) if request.GET.get("n", "").isdigit() else 0
    f = files[n] if n < len(files) else None
    if not f:
        raise Http404
    resp = FileResponse(f.open("rb"), content_type=mimetypes.guess_type(f.name)[0] or "image/jpeg")
    resp["Cache-Control"] = "private, max-age=3600"
    resp["X-Content-Type-Options"] = "nosniff"
    return resp


def _shared_itinerary(token):
    from .models import LeadItinerary

    return get_object_or_404(LeadItinerary.objects.select_related("enquiry__client", "enquiry__assigned_agent"), share_token=token)


def trip_proposal(request, token):
    """The designed proposal (cover, day cards, lodges, travel, pricing, payment) for the client, no login: the link is the secret."""
    from apps.core.models import CompanySetting

    from . import itinerary_pdf as slides

    it = _shared_itinerary(token)
    ctx = slides.build_context(it, CompanySetting.load(), web=token)
    ctx.update(print=False, pdf_url=reverse("public_trip_pdf", args=[token]))
    resp = render(request, "sales/itinerary_slides.html", ctx)
    resp["X-Robots-Tag"] = "noindex, nofollow"
    resp["Cache-Control"] = "private, no-store"
    return resp


def trip_pdf(request, token):
    """The designed PDF as a download, from the client's own link."""
    from apps.core.models import CompanySetting

    from . import itinerary_pdf as slides

    it = _shared_itinerary(token)
    try:
        data = slides.build_pdf(it, CompanySetting.load())
    except Exception:   # WeasyPrint or its system libraries are missing on this host
        return redirect("public_trip_proposal", token=token)
    resp = HttpResponse(data, content_type="application/pdf")
    resp["Content-Disposition"] = f'attachment; filename="itinerary-{it.enquiry.reference}.pdf"'
    resp["X-Robots-Tag"] = "noindex, nofollow"
    resp["Cache-Control"] = "private, no-store"
    return resp


def trip_file(request, token, name):
    """One of the itinerary's own pictures, for the public proposal. Anything not on this itinerary's list is a 404."""
    import base64
    import mimetypes

    from django.core.files.storage import default_storage
    from django.http import FileResponse

    from . import itinerary_pdf as slides

    it = _shared_itinerary(token)
    try:
        real = base64.urlsafe_b64decode(name + "=" * (-len(name) % 4)).decode()
    except Exception:
        raise Http404
    if real not in slides.shared_file_names(it) or not default_storage.exists(real):
        raise Http404
    resp = FileResponse(default_storage.open(real, "rb"), content_type=mimetypes.guess_type(real)[0] or "image/jpeg")
    resp["Cache-Control"] = "private, max-age=3600"
    resp["X-Content-Type-Options"] = "nosniff"
    return resp


@require_POST
def trip_respond(request, token):
    """The client says "I'd like to book this", asks for a change or asks a question, from their private link."""
    from .models import ClientResponse

    it = get_object_or_404(LeadItinerary.objects.select_related("enquiry__client", "enquiry__assigned_agent"), share_token=token)
    kind = request.POST.get("kind", "")
    try:
        services.record_client_response(it, kind, request.POST.get("message", ""), request.POST.get("hotel") if (request.POST.get("hotel") or "").isdigit() else None)
        ok, msg = True, {"book": "Thank you! Your consultant has been told and will contact you shortly to confirm.",
                         "change": "Got it. Your consultant will adjust the itinerary and get back to you.",
                         "question": "Thanks! Your consultant will reply shortly."}[kind]
    except ValueError as exc:
        ok, msg = False, str(exc)
    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return JsonResponse({"ok": ok, "message": msg}, status=200 if ok else 400)
    messages.add_message(request, messages.SUCCESS if ok else messages.ERROR, msg)
    return redirect("public_trip", token=token)


def trip_hotel_image(request, token, hotel_id):
    """A lodge option's cover photo for the public client page, served by link token."""
    import mimetypes

    from django.http import FileResponse

    it = get_object_or_404(LeadItinerary, share_token=token)
    h = get_object_or_404(it.hotels.select_related("photo_pack"), pk=hotel_id)
    cover = h.photo_pack.cover if h.photo_pack_id else None
    if not cover:
        raise Http404
    resp = FileResponse(cover.image.open("rb"), content_type=mimetypes.guess_type(cover.image.name)[0] or "image/jpeg")
    resp["Cache-Control"] = "private, max-age=3600"
    resp["X-Content-Type-Options"] = "nosniff"
    return resp


@require_POST
@module_required(SALES)
def itinerary_send(request, pk):
    it = _itinerary(request, pk, act=True)
    form = SendForm(request.POST)
    if form.is_valid():
        try:
            services.send_itinerary(it, form.cleaned_data["via"], request.user)
            messages.success(request, "Itinerary marked as sent. The lead moved to Itinerary sent.")
        except ValueError as exc:
            messages.error(request, str(exc))
    return redirect(it)


# ------------------------------------------------------------------------------------------------ bookings
class BookingList(ListScreen):
    module_key = SALES
    model = Booking
    filterset_class = BookingFilter
    search_fields = ["reference", "title", "client__full_name", "client__phone", "enquiry__reference"]
    sort_fields = ["booked_at", "travel_start", "total_amount", "status"]
    default_sort = "-booked_at"
    template_name = "sales/booking_list.html"
    partial_template = "sales/partials/booking_table.html"
    title = "Bookings"
    subtitle = "Accepted quotations, deposits and balances."
    export_name = "bookings"
    export_columns = [("Booking", "reference"), ("Client", "client.full_name"), ("Trip", "title"), ("Brought by", "enquiry.brought_by_label"), ("Remark", "enquiry.remark"),
                      ("Travel from", "travel_start"), ("Travel to", "travel_end"), ("Agent", "agent"),
                      ("Status", "get_status_display"), ("Currency", "currency"), ("Total", "total_amount"),
                      ("Paid", "amount_paid"), ("Balance", "balance"), ("Booked", "booked_at")]

    def get_base_queryset(self):
        return services.bookings_for(self.request.user)

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        live = services.bookings_for(self.request.user).exclude(status=BookingStatus.CANCELLED)
        today = timezone.localdate()
        ctx["kpis"] = {
            "count": live.count(), "pending": live.filter(status=BookingStatus.PENDING).count(),
            "value": services.sum_kes(live),
            "outstanding": sum((b.balance * b.fx_rate for b in live.exclude(status=BookingStatus.COMPLETED)),
                               Decimal("0")),
            "departing": live.filter(travel_start__gte=today, travel_start__lte=today + timedelta(days=14)).count()}
        return ctx


def _booking(request, pk, *, act=False):
    b = get_object_or_404(services.bookings_for(request.user), pk=pk)
    if act and not services.can_act_on(request.user, b.enquiry):
        raise PermissionDenied
    return b


@module_required(SALES)
def booking_detail(request, pk):
    b = _booking(request, pk)
    return render(request, "sales/booking_detail.html", {
        "b": b, "payments": b.payments.select_related("recorded_by"), "can_act": services.can_act_on(request.user, b.enquiry),
        "pay_form": PaymentForm(initial={"amount": b.deposit_amount if b.amount_paid == 0 else b.balance}),
        "status_form": BookingStatusForm(initial={"status": b.status}),
        "activities": b.enquiry.activities.select_related("user")[:15], "next_statuses": _next_statuses(b),
        "departures": _open_departures(b), "letter_templates": __import__("apps.sales.models", fromlist=["LetterTemplate"]).LetterTemplate.objects.all()[:8],
        "portal_url": request.build_absolute_uri(reverse("public_trip_portal", args=[b.portal_token])) if b.portal_token else "",
        "portal_wa": __import__("urllib.parse", fromlist=["quote"]).quote(f"Hello {b.client.full_name.split()[0]}, here is your trip page for {b.title}: payments, itinerary and what to pack. " + (request.build_absolute_uri(reverse("public_trip_portal", args=[b.portal_token])) if b.portal_token else ""))})


def _open_departures(b):
    from apps.trips.models import Departure, Stage

    if b.status == BookingStatus.CANCELLED:
        return []
    qs = Departure.objects.filter(stage__in=[Stage.PLANNED, Stage.READY],
                                  start_date__gte=timezone.localdate()).select_related("package")
    if b.package_id:
        qs = qs.filter(package_id=b.package_id)
    return qs.order_by("start_date")[:20]


def _next_statuses(b):
    flow = {BookingStatus.PENDING: [BookingStatus.CANCELLED],
            BookingStatus.DEPOSIT: [BookingStatus.CONFIRMED, BookingStatus.CANCELLED],
            BookingStatus.CONFIRMED: [BookingStatus.COMPLETED, BookingStatus.CANCELLED]}
    return [(s.value, s.label) for s in flow.get(b.status, [])]


@module_required(SALES)
def booking_edit(request, pk):
    b = _booking(request, pk, act=True)
    form = BookingEditForm(request.POST or None, instance=b)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Booking updated.")
        return redirect(b)
    return render(request, "components/form_page.html", {"form": form, "title": f"Edit {b.reference}",
                                                         "cancel_url": b.get_absolute_url()})


@require_POST
@module_required(SALES)
def booking_payment(request, pk):
    b = _booking(request, pk, act=True)
    form = PaymentForm(request.POST)
    if not form.is_valid():
        messages.error(request, "; ".join(e for errs in form.errors.values() for e in errs))
        return redirect(b)
    d = form.cleaned_data
    try:
        services.record_payment(b, request.user, amount=d["amount"], method=d["method"], reference=d["reference"],
                                received_at=d["received_at"], note=d["note"])
        messages.success(request, f"Payment of {b.currency} {d['amount']:,.2f} recorded.")
    except ValueError as exc:
        messages.error(request, str(exc))
    return redirect(b)


@require_POST
@module_required(SALES)
def booking_departure(request, pk):
    from apps.trips import services as trip_services
    from apps.trips.models import Departure

    b = _booking(request, pk, act=True)
    dep = get_object_or_404(Departure, pk=request.POST.get("departure") or 0)
    try:
        trip_services.attach_booking(b, dep, request.user)
        messages.success(request, f"Added to departure {dep.reference} ({dep.start_date:%d %b %Y}).")
    except ValueError as exc:
        messages.error(request, str(exc))
    return redirect(b)


@require_POST
@module_required(SALES)
def booking_status(request, pk):
    b = _booking(request, pk, act=True)
    form = BookingStatusForm(request.POST)
    if not form.is_valid():
        messages.error(request, "; ".join(e for errs in form.errors.values() for e in errs))
        return redirect(b)
    try:
        services.set_booking_status(b, form.cleaned_data["status"], request.user, form.cleaned_data["reason"])
        messages.success(request, f"Booking is now {b.get_status_display().lower()}.")
    except ValueError as exc:
        messages.error(request, str(exc))
    return redirect(b)


# ------------------------------------------------------------------------------------------------ agents & team
def _agent_page(request, agent: Employee, *, mine: bool):
    ctx = _ctx(request)
    start, end = ctx["start"], ctx["end"]
    row = analytics.agent_rows(start, end, [agent])[0]
    enq = Enquiry.objects.filter(assigned_agent=agent)
    open_leads = enq.filter(stage__in=OPEN_STAGES).select_related("client", "package", "destination") \
        .order_by("-deal_value")[:12]
    ctx.update(
        agent=agent, row=row, mine=mine, open_leads=open_leads, stages=analytics.stage_summary(enq),
        calls=CallLog.objects.filter(agent=agent).select_related("enquiry__client")[:8],
        tasks=FollowUp.objects.filter(agent=agent, done_at__isnull=True).select_related("enquiry__client")
        .order_by("due_at")[:8],
        bookings=Booking.objects.filter(agent=agent).exclude(status=BookingStatus.CANCELLED)
        .select_related("client").prefetch_related("payments")[:6],
        activity=LeadActivity.objects.filter(Q(user=agent.user) if agent.user_id else Q(pk__in=[]))
        .select_related("enquiry__client").order_by("-timestamp")[:10],
        charts={"revenue": analytics.monthly_revenue(6, [agent.pk])},
        cold=services.going_cold(enq.filter(stage__in=OPEN_STAGES)).select_related("client")[:5],
        breaches=services.sla_breaches(enq.filter(stage__in=OPEN_STAGES)).select_related("client")[:5],
        reassign_form=ReassignForm() if request.user.can_manage_sales else None)
    return render(request, "sales/agent_detail.html", ctx)


@module_required(SALES)
def me(request):
    agent = request.user.sales_agent_profile
    if agent is None:
        raise Http404("You don't have a staff profile yet. Ask an administrator to link your account.")
    return _agent_page(request, agent, mine=True)


@module_required(SALES)
def team(request):
    _need_team_view(request.user)
    ctx = _ctx(request)
    agents = Employee.objects.filter(is_sales_agent=True, is_active=True)
    rows = analytics.agent_rows(ctx["start"], ctx["end"], agents)
    sort = request.GET.get("sort", "revenue")
    if sort not in {"revenue", "won", "open", "pipeline", "conversion", "calls", "overdue", "breaches"}:
        sort = "revenue"
    rows.sort(key=lambda r: (-(r[sort] or 0), r["agent"].full_name))
    if request.GET.get("export") == "csv":
        return _csv_response("team-performance.csv", ["Agent", "Status", "Open leads", "Pipeline KES", "Assigned",
                                                      "Bookings", "Revenue KES", "Conversion %", "Quotes", "Calls",
                                                      "Overdue tasks", "SLA breaches"],
                             [[r["agent"].full_name, r["agent"].get_on_duty_display(), r["open"], r["pipeline"],
                               r["assigned"], r["won"], r["revenue"], r["conversion"], r["quotes"], r["calls"],
                               r["overdue"], r["breaches"]] for r in rows])
    ctx.update(rows=rows, totals=analytics.totals(rows), sort=sort,
               cols=[("revenue", "Revenue"), ("won", "Won"), ("open", "Open"), ("pipeline", "Pipeline"),
                     ("conversion", "Conv."), ("calls", "Calls"), ("overdue", "Overdue"), ("breaches", "SLA")],
               unassigned=Enquiry.objects.filter(assigned_agent__isnull=True, stage__in=OPEN_STAGES).count(),
               charts={"revenue": analytics.monthly_revenue(6)})
    return render(request, "sales/team.html", ctx)


@module_required(SALES)
def team_agent(request, pk):
    _need_team_view(request.user)
    agent = get_object_or_404(Employee.objects.filter(is_sales_agent=True), pk=pk)
    return _agent_page(request, agent, mine=False)


@require_POST
@module_required(SALES)
def team_reassign(request, pk):
    """Hand every open lead of one agent to another (leave cover, resignations)."""
    _need_manager(request.user)
    src = get_object_or_404(Employee, pk=pk)
    form = ReassignForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Choose who takes over the leads.")
        return redirect("sales:team_agent", pk=pk)
    dst = form.cleaned_data["agent"]
    if dst.pk == src.pk:
        messages.error(request, "Choose a different agent.")
        return redirect("sales:team_agent", pk=pk)
    if not dst.is_available:
        messages.error(request, f"{dst} is {dst.get_on_duty_display().lower()} and cannot take leads.")
        return redirect("sales:team_agent", pk=pk)
    n = 0
    for e in Enquiry.objects.filter(assigned_agent=src, stage__in=OPEN_STAGES):
        lead_services.assign_agent(e, dst, request.user, "manual", f"Cover from {src}")
        n += 1
    messages.success(request, f"{n} open lead{'s' if n != 1 else ''} moved to {dst}.")
    return redirect("sales:team_agent", pk=pk)


# ------------------------------------------------------------------------------------------------ assignment
@module_required(SALES)
def assignment(request):
    _need_team_view(request.user)
    cfg = SalesSettings.get()
    settings_form = SalesSettingsForm(request.POST or None, instance=cfg, prefix="cfg")
    if request.method == "POST":
        _need_manager(request.user)
        if settings_form.is_valid():
            settings_form.save()
            messages.success(request, "Settings saved.")
            return redirect("sales:assignment")
    today = timezone.localdate()
    agents = []
    for r in analytics.agent_rows(today, today, Employee.objects.filter(is_sales_agent=True, is_active=True)):
        a = r["agent"]
        todays = a.lead_assignments.filter(assigned_at__date=today).count()
        agents.append({"agent": a, "open": r["open"], "today": todays, "limit": a.daily_enquiry_limit,
                       "available": a.is_available, "pct": min(100, round(100 * todays / a.daily_enquiry_limit))
                       if a.daily_enquiry_limit else 0, "breaches": r["breaches"]})
    waiting = (Enquiry.objects.filter(assigned_agent__isnull=True, stage__in=OPEN_STAGES)
               .select_related("client", "package", "destination").order_by("created_at"))
    return render(request, "sales/assignment.html", {
        "settings_form": settings_form, "agents": agents, "waiting": waiting[:30], "waiting_count": waiting.count(),
        "rules": AssignmentRule.objects.select_related("agent", "destination"), "assign_agents": Employee.objects.filter(
            is_sales_agent=True, is_active=True), "can_manage": request.user.can_manage_sales,
        "breaches": services.sla_breaches().select_related("client", "assigned_agent")[:10]})


BUDGET_BANDS = {"low": (0, 50000), "mid": (50000, 150000), "high": (150000, 300000), "lux": (300000, None)}


@module_required(SALES)
def assign_leads(request):
    """Lead-handover desk for the Sales Manager (and the other lead distributors): filter, tick leads, hand them to an agent or let the rules choose."""
    if not request.user.can_distribute_leads:
        raise PermissionDenied
    g = request.GET
    status = g.get("status", "unassigned")
    qs = Enquiry.objects.select_related("client", "package", "destination", "assigned_agent")
    if status == "assigned":
        qs = qs.filter(assigned_agent__isnull=False, stage__in=OPEN_STAGES)
    elif status == "all":
        qs = qs.filter(stage__in=OPEN_STAGES)
    else:
        status = "unassigned"
        qs = qs.filter(assigned_agent__isnull=True, stage__in=OPEN_STAGES)
    if g.get("stage"):
        qs = qs.filter(stage=g["stage"])
    if g.get("source"):
        qs = qs.filter(source=g["source"])
    if g.get("destination", "").isdigit():
        qs = qs.filter(destination_id=int(g["destination"]))
    if g.get("region"):
        qs = qs.filter(destination__region=g["region"])
    band = BUDGET_BANDS.get(g.get("budget", ""))
    if band:
        qs = qs.filter(budget_max_pp__gte=band[0], **({"budget_max_pp__lt": band[1]} if band[1] else {}))
    days = {"today": 0, "week": 7, "month": 30}.get(g.get("added", ""))
    if days is not None:
        qs = qs.filter(created_at__date__gte=timezone.localdate() - timedelta(days=days))
    waiting_total = Enquiry.objects.filter(assigned_agent__isnull=True, stage__in=OPEN_STAGES).count()
    rows = []
    for e in qs.order_by("created_at")[:100]:
        sug = None
        if not e.assigned_agent_id:
            agent, _m, why = services.pick_agent(e)
            sug = {"agent": agent, "why": why} if agent else None
        rows.append({"e": e, "suggest": sug})
    today = timezone.localdate()
    caps = []
    for a in Employee.objects.filter(is_sales_agent=True, is_active=True).order_by("full_name"):
        n_today = a.lead_assignments.filter(assigned_at__date=today).count()
        caps.append({"agent": a, "today": n_today, "limit": a.daily_enquiry_limit, "open": Enquiry.objects.filter(assigned_agent=a, stage__in=OPEN_STAGES).count(),
                     "pct": min(100, round(100 * n_today / a.daily_enquiry_limit)) if a.daily_enquiry_limit else 0, "available": a.is_available})
    from apps.catalog.models import Destination
    return render(request, "sales/assign_leads.html", {
        "auto_on": services.SalesSettings.get().auto_assign_new, "rows": rows, "caps": caps, "status": status, "waiting_total": waiting_total, "shown": len(rows), "f": g,
        "stages": Stage.choices, "sources": source_choices(), "destinations": Destination.objects.order_by("name"), "regions": Destination.REGION_CHOICES,
        "bands": [("low", "Under 50K / person"), ("mid", "50K – 150K"), ("high", "150K – 300K"), ("lux", "300K and above")],
        "added_choices": [("today", "Today"), ("week", "Last 7 days"), ("month", "Last 30 days")]})


@require_POST
@module_required(SALES)
def assign_leads_bulk(request):
    if not request.user.can_distribute_leads:
        raise PermissionDenied
    ids = [int(i) for i in request.POST.getlist("ids") if i.isdigit()]
    back = redirect(request.POST.get("next") or "sales:assign_leads")
    if not ids:
        messages.warning(request, "Tick at least one lead first.")
        return back
    leads = list(Enquiry.objects.filter(pk__in=ids))
    agent_id = request.POST.get("agent", "")
    done, failed = 0, []
    if agent_id.isdigit():
        agent = get_object_or_404(Employee, pk=int(agent_id), is_sales_agent=True)
        for e in leads:
            try:
                lead_services.assign_agent(e, agent, request.user, "manual", "Assigned from Assign Leads")
                done += 1
            except ValueError as exc:
                failed.append(str(exc))
                break
        if done:
            messages.success(request, f"{done} lead{'s' if done != 1 else ''} assigned to {agent.full_name}.")
    else:
        for e in leads:
            try:
                services.auto_assign(e, request.user)
                done += 1
            except ValueError as exc:
                failed.append(str(exc))
                break
        if done:
            messages.success(request, f"{done} lead{'s' if done != 1 else ''} assigned by the rules and round-robin.")
    if failed:
        messages.warning(request, failed[0])
    return back


@require_POST
@module_required(SALES)
def assignment_auto(request):
    if not request.user.can_distribute_leads:
        raise PermissionDenied
    done, skipped = services.auto_assign_unassigned(request.user)
    if done:
        messages.success(request, f"{done} enquir{'ies' if done != 1 else 'y'} assigned by rules and round-robin.")
    if skipped or not done:
        messages.warning(request, "Some enquiries are still waiting: no agent is on duty with capacity left.")
    return redirect(request.POST.get("next") or "sales:assignment")


@require_POST
@module_required(SALES)
def assignment_one(request, pk):
    if not request.user.can_distribute_leads:
        raise PermissionDenied
    enquiry = get_object_or_404(Enquiry, pk=pk)
    try:
        agent, why = services.auto_assign(enquiry, request.user)
        messages.success(request, f"{enquiry.reference} → {agent} ({why}).")
    except ValueError as exc:
        messages.error(request, str(exc))
    return redirect("sales:assignment")


@module_required(SALES)
def rule_edit(request, pk=None):
    _need_manager(request.user)
    obj = get_object_or_404(AssignmentRule, pk=pk) if pk else None
    form = AssignmentRuleForm(request.POST or None, instance=obj)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Rule saved.")
        return redirect("sales:assignment")
    return render(request, "components/form_page.html", {
        "form": form, "title": f"Edit rule: {obj}" if obj else "New assignment rule",
        "subtitle": "The first active rule (lowest priority number) that matches and whose agent is on duty wins. "
                    "Otherwise the agent who has waited longest gets the lead.",
        "cancel_url": reverse("sales:assignment")})


@require_POST
@module_required(SALES)
def rule_delete(request, pk):
    _need_manager(request.user)
    get_object_or_404(AssignmentRule, pk=pk).delete()
    messages.success(request, "Rule deleted.")
    return redirect("sales:assignment")


# ------------------------------------------------------------------------------------------------ double allocation
@module_required(SALES)
def double_allocation(request):
    _need_team_view(request.user)
    from .models import DoubleAllocation

    services.detect_double_allocations()
    cards = []
    for da in DoubleAllocation.objects.filter(status=DoubleAllocation.OPEN).select_related("client"):
        enquiries = list(da.client.enquiries.filter(stage__in=OPEN_STAGES, assigned_agent__isnull=False)
                         .select_related("assigned_agent", "package"))
        suggested = services.suggested_owner(da.client)
        rows = []
        for e in enquiries:
            first = e.activities.filter(activity_type__in=list(LeadActivity.CONTACT_TYPES)).order_by("timestamp").first()
            rows.append({"e": e, "calls": e.calls.count(), "quotes": e.quotations.count(), "activity": e.activities.count(),
                         "first_contact": first.timestamp if first else None, "suggested": e.assigned_agent_id == getattr(suggested, "pk", None)})
        cards.append({"da": da, "rows": rows, "suggested": suggested})
    resolved = DoubleAllocation.objects.filter(status=DoubleAllocation.RESOLVED).select_related("client", "kept_agent")[:10]
    month = timezone.localdate().replace(day=1)
    return render(request, "sales/double_allocation.html", {
        "cards": cards, "resolved": resolved, "can_manage": request.user.can_manage_sales,
        "resolved_total": DoubleAllocation.objects.filter(status=DoubleAllocation.RESOLVED).count(),
        "resolved_month": DoubleAllocation.objects.filter(status=DoubleAllocation.RESOLVED, resolved_at__date__gte=month).count()})


@require_POST
@module_required(SALES)
def double_allocation_resolve(request, pk):
    _need_manager(request.user)
    from .models import DoubleAllocation

    da = get_object_or_404(DoubleAllocation, pk=pk)
    agent = get_object_or_404(Employee, pk=request.POST.get("agent") or 0)
    try:
        services.resolve_double_allocation(da, agent, request.user, request.POST.get("reason", ""))
        messages.success(request, f"{da.client.full_name} stays with {agent.full_name}.")
    except ValueError as exc:
        messages.error(request, str(exc))
    return redirect("sales:double_allocation")


@require_POST
@module_required(SALES)
def double_allocation_apply_suggested(request):
    _need_manager(request.user)
    from .models import DoubleAllocation

    n = 0
    for da in DoubleAllocation.objects.filter(status=DoubleAllocation.OPEN):
        agent = services.suggested_owner(da.client)
        if agent:
            try:
                services.resolve_double_allocation(da, agent, request.user, "Suggested owner applied")
                n += 1
            except ValueError:
                pass
    messages.success(request, f"{n} conflict(s) resolved with the suggested owner.")
    return redirect("sales:double_allocation")


# ------------------------------------------------------------------------------------------------ targets
@module_required(SALES)
def targets(request):
    _need_team_view(request.user)
    today = timezone.localdate()
    try:
        year, month = int(request.GET.get("year", today.year)), int(request.GET.get("month", today.month))
        first = today.replace(year=year, month=month, day=1)
    except ValueError:
        first = today.replace(day=1)
    form = TargetForm(request.POST or None, initial={"year": first.year, "month": first.month})
    if request.method == "POST":
        _need_manager(request.user)
        if form.is_valid():
            d = form.cleaned_data
            SalesTarget.objects.update_or_create(
                agent=d["agent"], year=d["year"], month=d["month"],
                defaults={"revenue_target": d["revenue_target"], "bookings_target": d["bookings_target"],
                          "calls_target": d["calls_target"]})
            messages.success(request, f"Target saved for {d['agent']}.")
            return redirect(f"{reverse('sales:targets')}?year={d['year']}&month={d['month']}")
    last_day = (first.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    rows = analytics.agent_rows(first, min(last_day, today) if first <= today else first,
                                Employee.objects.filter(is_sales_agent=True, is_active=True))
    for r in rows:
        r["t"] = SalesTarget.objects.filter(agent=r["agent"], year=first.year, month=first.month).first()
        t = r["t"]
        r["rev_pct"] = round(100 * r["revenue"] / t.revenue_target) if t and t.revenue_target else None
        r["bk_pct"] = round(100 * r["won"] / t.bookings_target) if t and t.bookings_target else None
        r["call_pct"] = round(100 * r["calls"] / t.calls_target) if t and t.calls_target else None
    prev = (first - timedelta(days=1)).replace(day=1)
    nxt = last_day + timedelta(days=1)
    return render(request, "sales/targets.html", {
        "rows": rows, "first": first, "form": form, "prev": prev, "next": nxt, "can_manage": request.user.can_manage_sales,
        "team_target": sum((r["t"].revenue_target for r in rows if r["t"]), Decimal("0")),
        "team_revenue": sum((r["revenue"] for r in rows), Decimal("0"))})


@require_POST
@module_required(SALES)
def targets_copy(request):
    """Copy last month's targets forward to the month shown."""
    _need_manager(request.user)
    try:
        year, month = int(request.POST["year"]), int(request.POST["month"])
    except (KeyError, ValueError):
        raise Http404
    cur = timezone.localdate().replace(year=year, month=month, day=1)
    prev = (cur - timedelta(days=1)).replace(day=1)
    n = 0
    for t in SalesTarget.objects.filter(year=prev.year, month=prev.month):
        _, created = SalesTarget.objects.get_or_create(
            agent=t.agent, year=year, month=month,
            defaults={"revenue_target": t.revenue_target, "bookings_target": t.bookings_target,
                      "calls_target": t.calls_target})
        n += created
    messages.success(request, f"Copied {n} target{'s' if n != 1 else ''} from {prev:%B %Y}.")
    return redirect(f"{reverse('sales:targets')}?year={year}&month={month}")


# ------------------------------------------------------------------------------------------------ reports
def _csv_response(name, header, rows):
    resp = HttpResponse(content_type="text/csv")
    resp["Content-Disposition"] = f'attachment; filename="{name}"'
    w = csv.writer(resp)
    w.writerow(header)
    w.writerows(rows)
    return resp


@module_required(SALES)
def reports(request):
    _need_team_view(request.user)
    ctx = _ctx(request)
    start, end = ctx["start"], ctx["end"]
    enq = Enquiry.objects.all()
    rows = analytics.agent_rows(start, end)
    src = analytics.source_rows(enq, start, end)
    if request.GET.get("export") == "sources":
        return _csv_response("sources.csv", ["Source", "Enquiries", "Booked", "Conversion %", "Revenue KES"],
                             [[r["label"], r["enquiries"], r["won"], r["conversion"], r["revenue"]] for r in src])
    top_packages = analytics.package_rows(start, end)
    ctx.update(funnel=analytics.funnel(enq, start, end), sources=src, top_packages=top_packages, lost=analytics.lost_reasons(enq, start, end),
               rows=sorted(rows, key=lambda r: -r["revenue"]), totals=analytics.totals(rows),
               charts={"revenue": analytics.monthly_revenue(6),
                       "packages": {"labels": [r["name"][:26] for r in top_packages[:8]], "values": [r["bookings"] for r in top_packages[:8]], "label": "Bookings"},
                       "sources": {"labels": [r["label"] for r in src], "values": [r["enquiries"] for r in src],
                                   "codes": [r["code"] for r in src]}})
    return render(request, "sales/reports.html", ctx)


# ------------------------------------------------------------------------------------------------ client trip page
@require_POST
@module_required(SALES)
def booking_portal(request, pk):
    b = _booking(request, pk, act=True)
    if request.POST.get("action") == "revoke":
        services.revoke_portal(b, request.user)
        messages.success(request, "Trip page switched off. The old address no longer works.")
    else:
        services.enable_portal(b, request.user)
        messages.success(request, "Trip page ready. Copy the link or send it on WhatsApp.")
    return redirect(b)


def trip_portal(request, token):
    """The client's own page for a booked trip (no login): payments and how to pay, the itinerary, what to bring, and a rating once the trip is over."""
    from django.db.models import F

    from apps.core.models import CompanySetting

    from . import portal
    from .models import Booking

    b = get_object_or_404(Booking.objects.select_related("client", "agent", "package", "quotation", "enquiry", "departure__guide", "departure__vehicle"), portal_token=token)
    Booking.objects.filter(pk=b.pk).update(portal_views=F("portal_views") + 1)
    it = portal.itinerary_for(b)
    days = list(it.days.select_related("destination", "accommodation")) if it else []
    company = CompanySetting.load()
    resp = render(request, "sales/portal.html", {
        "b": b, "it": it, "days": days, "company": company, "agent": b.agent, "rows": portal.payment_rows(b), "documents": portal.lines(company.documents_list) or portal.DOCUMENTS, "packing": portal.lines(company.packing_list) or portal.PACKING,
        "over": portal.trip_over(b), "feedback": getattr(b, "feedback", None), "first": b.client.full_name.split()[0],
        "payments": b.payments.all()[:20], "token": token, "flights": b.flights.all(), "dep": b.departure,
        "left": (b.travel_start - timezone.localdate()).days if b.travel_start else None})
    resp["X-Robots-Tag"] = "noindex, nofollow"
    resp["Cache-Control"] = "private, no-store"
    return resp


def trip_portal_calendar(request, token):
    """The trip as a calendar file the client can open in Google, Apple or Outlook calendar."""
    from apps.core.models import CompanySetting

    from . import portal, vouchers
    from .models import Booking

    b = get_object_or_404(Booking, portal_token=token)
    resp = HttpResponse(vouchers.ics(b, portal.itinerary_for(b), CompanySetting.load()), content_type="text/calendar; charset=utf-8")
    resp["Content-Disposition"] = f'attachment; filename="trip-{b.reference}.ics"'
    resp["Cache-Control"] = "private, no-store"
    return resp


@require_POST
def trip_portal_feedback(request, token):
    from . import portal
    from .models import Booking

    b = get_object_or_404(Booking.objects.select_related("client", "agent__user", "enquiry"), portal_token=token)
    if not portal.trip_over(b):
        messages.error(request, "You can rate your trip once you are back.")
        return redirect("public_trip_portal", token=token)
    try:
        services.submit_feedback(b, rating=request.POST.get("rating", ""), nps=request.POST.get("nps", ""), guide_rating=request.POST.get("guide_rating", ""),
                                 lodge_rating=request.POST.get("lodge_rating", ""), comment=request.POST.get("comment", ""), may_quote=request.POST.get("may_quote") == "on")
        messages.success(request, "Thank you! Your feedback means a lot to us.")
    except (ValueError, TypeError) as exc:
        messages.error(request, str(exc) if isinstance(exc, ValueError) and "feedback" in str(exc) else "Please choose a score for each question.")
    return redirect("public_trip_portal", token=token)


@module_required(SALES)
def feedback_list(request):
    """What clients say after their trips, with the recommend score the managers steer by."""
    from django.db.models import Avg

    from .models import TripFeedback

    if not (request.user.can_manage_sales or request.user.sees_all_sales):
        raise PermissionDenied
    qs = TripFeedback.objects.select_related("booking__client", "booking__agent", "booking__package")
    low = request.GET.get("low") == "1"
    shown = qs.filter(rating__lte=3) | qs.filter(nps__lte=6) if low else qs
    n = qs.count()
    prom = qs.filter(nps__gte=9).count()
    det = qs.filter(nps__lte=6).count()
    avg = qs.aggregate(a=Avg("rating"), g=Avg("guide_rating"), l=Avg("lodge_rating"))
    return render(request, "sales/feedback.html", {
        "rows": shown.distinct()[:100], "n": n, "nps": round(100 * (prom - det) / n) if n else None, "avg": avg, "low": low,
        "promoters": prom, "detractors": det, "passives": n - prom - det})


# ------------------------------------------------------------------------------------------------ letters
@module_required(SALES)
def letters(request):
    """The team's standard letters: edit the wording, then fill one in for a booking from its page."""
    from . import letters as L
    from .models import LetterTemplate

    can_edit = request.user.can_manage_sales or request.user.is_superuser
    if not LetterTemplate.objects.exists():
        for name, subject, body in L.DEFAULTS:
            LetterTemplate.objects.create(name=name, subject=subject, body=body)
    if request.method == "POST":
        if not can_edit:
            raise PermissionDenied
        pk = request.POST.get("id", "")
        name, body = (request.POST.get("name") or "").strip()[:120], (request.POST.get("body") or "").strip()
        if not name or not body:
            messages.error(request, "A letter needs a name and some words.")
        else:
            fields = {"name": name, "subject": (request.POST.get("subject") or "").strip()[:160], "body": body[:6000]}
            if request.POST.get("action") == "delete" and pk.isdigit():
                LetterTemplate.objects.filter(pk=pk).delete()
                messages.success(request, "Letter removed.")
            elif pk.isdigit():
                LetterTemplate.objects.filter(pk=pk).update(**fields)
                messages.success(request, "Saved.")
            else:
                LetterTemplate.objects.create(created_by=request.user, **fields)
                messages.success(request, "Letter added.")
        return redirect("sales:letters")
    edit = LetterTemplate.objects.filter(pk=request.GET.get("edit")).first() if (request.GET.get("edit") or "").isdigit() else None
    return render(request, "sales/letters.html", {"items": LetterTemplate.objects.all(), "edit": edit, "can_edit": can_edit, "tokens": L.TOKENS})


@module_required(SALES)
def letter_generate(request, booking_id, pk):
    """A letter filled in for one booking: copy it, print it, or open it in the mail app. Opening it is noted on the lead."""
    from apps.core.models import CompanySetting

    from . import letters as L
    from .models import LetterTemplate

    b = _booking(request, booking_id)
    t = get_object_or_404(LetterTemplate, pk=pk)
    company = CompanySetting.load()
    ctx = L.context(b, company)
    body, subject = L.merge(t.body, ctx), L.merge(t.subject, ctx)
    if request.GET.get("print") == "1":
        return render(request, "sales/letter_print.html", {"b": b, "t": t, "body": body, "subject": subject, "company": company, "today": timezone.localdate()})
    return render(request, "sales/letter_generate.html", {"b": b, "t": t, "body": body, "subject": subject, "templates": LetterTemplate.objects.all(),
                                                          "mailto": f"mailto:{b.client.email}?subject={quote(subject)}&body={quote(body)}" if b.client.email else ""})
