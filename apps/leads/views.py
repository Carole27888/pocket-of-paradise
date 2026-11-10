import json

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.accounts import roles
from apps.accounts.permissions import is_marketing_scoped, is_scoped_agent, module_required
from apps.core.lists import ListScreen
from apps.people.models import Employee

from . import services
from .filters import EnquiryFilter
from .forms import AssignForm, EnquiryCreateForm, EnquiryEditForm, NoteForm, StageForm
from .models import OPEN_STAGES, Enquiry, Stage


def visible_enquiries(user):
    qs = Enquiry.objects.select_related("client", "package", "destination", "assigned_agent")
    if is_scoped_agent(user):
        qs = qs.filter(assigned_agent__user=user)
    elif is_marketing_scoped(user):
        qs = qs.filter(source__in=roles.SOCIAL_SOURCES)
    return qs


def _check_access(request, enquiry):
    if not visible_enquiries(request.user).filter(pk=enquiry.pk).exists():
        raise PermissionDenied


def _toast(resp, text, level="success"):
    resp["HX-Trigger"] = json.dumps({"toast": {"text": text, "level": level}})
    return resp


class EnquiryList(ListScreen):
    shared_with = ("sales",)
    model = Enquiry
    filterset_class = EnquiryFilter
    search_fields = ["reference", "client__full_name", "client__phone", "client__email", "destination_text",
                     "package__title", "destination__name"]
    sort_fields = ["created_at", "client__full_name", "stage", "stage_changed_at", "last_contact_at"]
    default_sort = "-created_at"
    template_name = "leads/enquiry_list.html"
    partial_template = "leads/partials/enquiry_table.html"
    title = "Enquiries"
    subtitle = "Every enquiry from every channel, in one shared record used by Front Desk, Sales and Marketing."
    export_name = "enquiries"
    export_columns = [("Reference", "reference"), ("Date", "created_at"), ("Client", "client.full_name"),
                      ("Phone", "client.phone"), ("Source", "get_source_display"), ("Brought by", "brought_by_label"), ("Interest", "interest_label"),
                      ("Travel dates", "travel_dates_label"), ("Adults", "adults"), ("Children", "children"), ("Remark", "remark"),
                      ("Purpose", "get_purpose_display"), ("Party", "party_label"), ("Stage", "get_stage_display"),
                      ("Quotation", "get_quotation_status_display"), ("Agent", "assigned_agent"),
                      ("Days in stage", "days_in_stage")]

    def get_base_queryset(self):
        return visible_enquiries(self.request.user)

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        base = visible_enquiries(self.request.user)
        counts = dict(base.values_list("stage").annotate(n=Count("pk")))
        ctx["stage_tabs"] = [{"key": "", "label": "All", "count": sum(counts.values())}] + [
            {"key": s.value, "label": s.label, "count": counts.get(s.value, 0)} for s in Stage
        ]
        ctx["current_stage"] = self.request.GET.get("stage", "")
        ctx["stage_choices"] = Stage.choices
        ctx["unassigned"] = base.filter(assigned_agent__isnull=True).exclude(stage__in=["lost", "completed"]).count()
        return ctx


@module_required("operations", "sales")
def enquiry_detail(request, pk):
    enquiry = get_object_or_404(Enquiry.objects.select_related("client", "package", "destination", "assigned_agent"),
                                pk=pk)
    _check_access(request, enquiry)
    ctx = {
        "enquiry": enquiry,
        "activities": enquiry.activities.select_related("user")[:50],
        "assignments": enquiry.assignments.select_related("agent", "assigned_by"),
        "party": enquiry.party_members.all(),
        "other_enquiries": enquiry.client.enquiries.exclude(pk=enquiry.pk).select_related("assigned_agent")[:5],
        "stage_form": StageForm(initial={"stage": enquiry.stage}),
        "assign_form": AssignForm(initial={"agent": enquiry.assigned_agent_id}),
        "note_form": NoteForm(),
        "can_edit": request.user.can_edit_operations or (
            enquiry.assigned_agent and enquiry.assigned_agent.user_id == request.user.pk),
    }
    if request.user.can_work_sales or request.user.sees_all_sales:
        from apps.sales import services as sales_services

        ctx.update(
            can_sell=sales_services.can_act_on(request.user, enquiry),
            quotations=enquiry.quotations.all()[:6], itineraries=enquiry.itineraries.all()[:4], calls=enquiry.calls.select_related("agent")[:5],
            followups=enquiry.followups.filter(done_at__isnull=True).order_by("due_at")[:5],
            booking=getattr(enquiry, "booking", None))
        from apps.insights import matching

        ctx.update(nba=matching.next_action(enquiry), matches=matching.match_packages(enquiry, 3), journey=sales_services.journey(enquiry))
    return render(request, "leads/enquiry_detail.html", ctx)


@require_POST
@module_required("operations", "sales")
def enquiry_draft(request, pk):
    """A short WhatsApp draft for the agent to check and send. Nothing is sent from here."""
    from apps.insights import matching

    enquiry = get_object_or_404(Enquiry.objects.select_related("client", "assigned_agent"), pk=pk)
    _check_access(request, enquiry)
    nba = matching.next_action(enquiry)
    d = matching.draft_message(enquiry, nba["action"])
    return JsonResponse({"text": d["text"], "ai": d["ai"], "wa": __import__("apps.core.phone", fromlist=["x"]).normalise_phone(enquiry.client.phone).lstrip("+")})


@module_required("operations", "sales")
def enquiry_create(request):
    if not request.user.can_capture_enquiries:
        raise PermissionDenied
    initial = {"source": request.GET.get("source", "whatsapp"), "brought_by": getattr(request.user, "employee", None) and request.user.employee.pk}
    for key in ("package", "destination"):
        if request.GET.get(key, "").isdigit():
            initial[key] = int(request.GET[key])
    if request.GET.get("client", "").isdigit():
        from apps.contacts.models import Client

        c = Client.objects.alive().filter(pk=request.GET["client"]).first()
        if c:
            initial.update(full_name=c.full_name, phone=c.phone, email=c.email, nationality=c.nationality)
    form = EnquiryCreateForm(request.POST or None, initial=initial)
    mine = request.user.sales_agent_profile if request.user.has_role(roles.SALES_AGENT) else None
    match = None
    if request.method == "GET" and initial.get("phone"):
        match = services.match_client(initial["phone"], initial.get("email", ""))
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        enquiry = services.create_enquiry(
            client_data={"full_name": d["full_name"], "phone": d["phone"], "email": d["email"] or "",
                         "nationality": d["nationality"] or ""},
            enquiry_data={"source": d["source"], "purpose": d["purpose"], "package": d["package"],
                          "destination": d["destination"] or (d["package"].destinations.first() if d["package"] else None),
                          "travel_date_from": d["travel_date_from"], "adults": d["adults"],
                          "children": d["children"], "budget_text": d["budget_text"], "notes": d["notes"],
                          "brought_by": d["brought_by"], "remark": d["remark"],
                          "captured_via": "agent" if request.user.has_role(roles.SALES_AGENT) else "front_desk"},
            user=request.user, auto_assign=not d["assigned_agent"] and not mine,
        )
        if d["assigned_agent"] and request.user.can_distribute_leads:
            services.assign_agent(enquiry, d["assigned_agent"], request.user, "manual", "Initial assignment")
        elif mine and mine.is_available:  # an agent who captures a lead owns it
            services.assign_agent(enquiry, mine, request.user, "manual", "Captured by agent")
        messages.success(request, f"Enquiry {enquiry.reference} saved"
                         + (" for a returning client." if enquiry.is_returning_client else "."))
        return redirect(enquiry)
    agents = Employee.objects.filter(is_sales_agent=True, is_active=True).annotate(
        open_count=Count("enquiries", filter=Q(enquiries__stage__in=OPEN_STAGES)))
    if request.method == "POST" and not match:
        match = services.match_client(request.POST.get("phone", ""), request.POST.get("email", ""))
    return render(request, "leads/enquiry_form.html", {"form": form, "match": match, "agents": agents,
                                                       "primary_sources": ["whatsapp", "walk_in", "call", "dm"]})


@module_required("operations", "sales")
def returning_check(request):
    """HTMX: live 'Returning client' banner while typing the phone/email."""
    m = services.match_client(request.GET.get("phone", ""), request.GET.get("email", ""))
    return render(request, "leads/partials/returning_banner.html", {"match": m})


@module_required("operations", "sales")
def enquiry_edit(request, pk):
    enquiry = get_object_or_404(Enquiry, pk=pk)
    _check_access(request, enquiry)
    form = EnquiryEditForm(request.POST or None, instance=enquiry)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Enquiry updated.")
        return redirect(enquiry)
    return render(request, "components/form_page.html",
                  {"form": form, "title": f"Edit {enquiry.reference}", "cancel_url": enquiry.get_absolute_url()})


@require_POST
@module_required("operations", "sales")
def enquiry_stage(request, pk):
    enquiry = get_object_or_404(Enquiry, pk=pk)
    _check_access(request, enquiry)
    form = StageForm(request.POST)
    if not form.is_valid():
        return _toast(HttpResponse(status=204), "; ".join(e for errs in form.errors.values() for e in errs), "danger")
    from apps.sales import services as sales_services

    target = form.cleaned_data["stage"]
    if sales_services.quote_needed(enquiry, target):   # no quotation on record: ask the user to create one first
        resp = HttpResponse(status=204)
        resp["HX-Trigger"] = json.dumps({"needQuote": sales_services.quote_prompt(enquiry, target)})
        return resp
    services.change_stage(enquiry, target, request.user, form.cleaned_data["lost_reason"])
    resp = HttpResponse(status=204)
    resp["HX-Refresh"] = "true"
    messages.success(request, f"Moved to {enquiry.get_stage_display()}.")
    return resp


@require_POST
@module_required("operations", "sales")
def enquiry_bulk_stage(request):
    """Move the ticked enquiries to one stage. Anything the user can't see is silently ignored."""
    if request.user.is_read_only:
        raise PermissionDenied
    ids = [int(i) for i in request.POST.getlist("ids") if i.isdigit()][:200]
    form = StageForm(request.POST)
    if not ids or not form.is_valid():
        msg = "Tick at least one enquiry." if not ids else "; ".join(e for errs in form.errors.values() for e in errs)
        return _toast(HttpResponse(status=204), msg, "danger")
    stage, reason = form.cleaned_data["stage"], form.cleaned_data["lost_reason"]
    from apps.sales import services as sales_services

    moved = skipped = unquoted = 0
    for enquiry in visible_enquiries(request.user).filter(pk__in=ids):
        if enquiry.stage == stage:
            skipped += 1
            continue
        if sales_services.quote_needed(enquiry, stage):
            unquoted += 1
            continue
        services.change_stage(enquiry, stage, request.user, reason)
        moved += 1
    label = dict(Stage.choices)[stage]
    messages.success(request, f"{moved} enquir{'y' if moved == 1 else 'ies'} moved to {label}."
                     + (f" {skipped} already there." if skipped else ""))
    if unquoted:
        messages.warning(request, f"{unquoted} not moved: no quotation yet. Create a quotation for each first.")
    resp = HttpResponse(status=204)
    resp["HX-Refresh"] = "true"
    return resp


@require_POST
@module_required("operations", "sales")
def enquiry_assign(request, pk):
    enquiry = get_object_or_404(Enquiry, pk=pk)
    if not request.user.can_distribute_leads:
        raise PermissionDenied
    form = AssignForm(request.POST)
    if form.is_valid():
        try:
            services.assign_agent(enquiry, form.cleaned_data["agent"], request.user, "manual")
        except ValueError as exc:
            return _toast(HttpResponse(status=204), str(exc), "danger")
        messages.success(request, f"Assigned to {form.cleaned_data['agent']}.")
    resp = HttpResponse(status=204)
    resp["HX-Refresh"] = "true"
    return resp


@require_POST
@module_required("operations", "sales")
def enquiry_note(request, pk):
    enquiry = get_object_or_404(Enquiry, pk=pk)
    _check_access(request, enquiry)
    form = NoteForm(request.POST)
    if form.is_valid():
        services.log_activity(enquiry, form.cleaned_data["activity_type"], form.cleaned_data["details"], request.user)
    activities = enquiry.activities.select_related("user")[:50]
    resp = render(request, "leads/partials/timeline.html", {"activities": activities, "enquiry": enquiry})
    return _toast(resp, "Activity logged.")


@module_required("operations", "sales")
def agent_load(request):
    """JSON for the assignment picker: agents, duty status and today's load."""
    from django.utils import timezone

    today = timezone.localdate()
    data = []
    for a in Employee.objects.filter(is_sales_agent=True, is_active=True):
        today_count = a.lead_assignments.filter(assigned_at__date=today).count()
        data.append({"id": a.pk, "name": a.full_name, "status": a.get_on_duty_display(),
                     "available": a.is_available, "today": today_count, "limit": a.daily_enquiry_limit})
    return JsonResponse({"agents": data})


# ------------------------------------------------------------------------------------------------ lead sources
def _can_manage_sources(user):
    return user.is_superuser or user.has_role(roles.ADMIN, roles.COO)


@module_required("operations", "sales", "ict")
def lead_sources(request):
    """The COO adds sources beyond the built-in ones (an expo, a roadshow, a radio ad); they appear wherever a source is picked."""
    from django.utils.text import slugify

    from .models import CustomSource, Source

    can = _can_manage_sources(request.user)
    if request.method == "POST":
        if not can:
            raise PermissionDenied
        act = request.POST.get("action")
        if act == "add":
            label = (request.POST.get("label") or "").strip()[:60]
            code = slugify(label).replace("-", "_")[:30]
            if not label or not code:
                messages.error(request, "Give the source a name, for example Nairobi Travel Expo.")
            elif code in Source.values or CustomSource.objects.filter(code=code).exists():
                messages.error(request, f"There is already a source called {label}.")
            else:
                CustomSource.objects.create(code=code, label=label, badge=(request.POST.get("badge") or "").strip()[:3],
                                            created_by=request.user)
                messages.success(request, f"{label} added. It now shows wherever a lead source is chosen.")
        else:
            src = get_object_or_404(CustomSource, pk=request.POST.get("pk") or 0)
            if act == "toggle":
                src.is_active = not src.is_active
                src.save(update_fields=["is_active"])
                messages.success(request, f"{src.label} {'is in use again' if src.is_active else 'hidden from new leads'}.")
            elif act == "delete":
                if src.lead_count:
                    messages.error(request, f"{src.label} has {src.lead_count} lead(s), so it can only be hidden, not deleted.")
                else:
                    src.delete()
                    messages.success(request, "Source deleted.")
        return redirect("leads:sources")
    rows = list(CustomSource.objects.all())
    built_in = [{"code": c, "label": l, "count": Enquiry.objects.filter(source=c).count()} for c, l in Source.choices]
    return render(request, "leads/sources.html", {"rows": rows, "built_in": built_in, "can": can})
