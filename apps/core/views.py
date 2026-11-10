import json
import mimetypes
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.db.models.functions import TruncDate
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts import roles
from apps.accounts.permissions import module_required, role_required
from apps.catalog.models import Destination, TourPackage
from apps.contacts.models import Client
from apps.fleet.models import Vehicle
from apps.leads.models import OPEN_STAGES, Enquiry, Source, Stage
from apps.leads.views import visible_enquiries
from apps.people.models import Employee

from .db_router import read_db
from .forms import CompanyForm
from .jobs.models import Job
from .jobs.registry import SCHEDULES
from .jobs.runner import last_heartbeat
from .models import CompanySetting, Currency, ExchangeRate, ScheduleState
from .modules import MODULES, modules_for, user_can_access_module


@login_required
def home(request):
    allowed = modules_for(request.user)
    available = [m for m in allowed if m.available]
    if request.GET.get("source") == "pwa" and len(available) == 1:
        return redirect(available[0].url_for(request.user))
    tiles = [{"module": m, "allowed": True, "url": m.url_for(request.user)} for m in allowed if m.key != "me"]   # My Space lives in the menu itself
    from .home_widgets import widgets_for

    briefing = None
    try:
        from apps.coach.services import ensure_briefing

        ensure_briefing(request.user)
        from apps.coach.models import CoachMessage

        emp = getattr(request.user, "employee", None)
        if emp is not None:
            briefing = CoachMessage.objects.filter(employee=emp, kind="briefing", day=timezone.localdate()).first()
    except Exception:   # the main menu must always load
        briefing = None
    from .home_widgets import setup_checklist

    try:
        setup = setup_checklist(request.user)
    except Exception:
        setup = None
    from apps.notifications.models import announcements_for

    try:
        notices = announcements_for(request.user, 2)
    except Exception:   # e.g. a migration not yet applied: the main menu must still open
        notices = []
    return render(request, "core/home.html", {"tiles": tiles, "day_chips": widgets_for(request.user), "briefing": briefing, "setup": setup,
                                              "notices": notices})


@module_required("operations")
def ops_dashboard(request):
    db = read_db()
    now = timezone.now()
    today = timezone.localdate()
    enq = visible_enquiries(request.user).using(db)
    month_start = today.replace(day=1)
    last_month_start = (month_start - timedelta(days=1)).replace(day=1)
    this_month = enq.filter(created_at__date__gte=month_start).count()
    last_month = enq.filter(created_at__date__gte=last_month_start, created_at__date__lt=month_start).count()
    open_qs = enq.filter(stage__in=OPEN_STAGES)
    stage_counts = dict(enq.values_list("stage").annotate(n=Count("pk")))
    source_counts = list(enq.filter(created_at__gte=now - timedelta(days=30)).values("source")
                         .annotate(n=Count("pk")).order_by("-n"))
    from apps.leads.models import source_labels

    src_labels = source_labels()
    days = [today - timedelta(days=i) for i in range(13, -1, -1)]
    per_day = dict(enq.filter(created_at__date__gte=days[0]).annotate(d=TruncDate("created_at"))
                   .values_list("d").annotate(n=Count("pk")))
    vehicles = Vehicle.objects.using(db)
    soon = today + timedelta(days=30)
    kpis = {
        "enquiries_month": this_month,
        "enquiries_delta": this_month - last_month,
        "open": open_qs.count(),
        "unassigned": open_qs.filter(assigned_agent__isnull=True).count(),
        "clients": Client.objects.using(db).alive().count(),
        "international": Client.objects.using(db).alive().filter(is_international=True).count(),
        "packages": TourPackage.objects.using(db).alive().filter(status__in=["published", "seasonal"]).count(),
        "destinations": Destination.objects.using(db).alive().count(),
        "vehicles_available": vehicles.filter(status=Vehicle.AVAILABLE).count(),
        "vehicles_out": vehicles.filter(status=Vehicle.OUT).count(),
        "vehicles_total": vehicles.count(),
        "guides_out": Employee.objects.using(db).filter(on_duty=Employee.OUT_ON_TRIP).count(),
    }
    charts = {
        "trend": {"labels": [f"{d.day} {d:%b}" for d in days], "values": [per_day.get(d, 0) for d in days]},
        "sources": {"labels": [src_labels.get(s["source"], s["source"]) for s in source_counts],
                    "values": [s["n"] for s in source_counts], "codes": [s["source"] for s in source_counts]},
        "stages": {"labels": [s.label for s in Stage], "values": [stage_counts.get(s.value, 0) for s in Stage],
                   "codes": [s.value for s in Stage]},
    }
    top_dest = (Destination.objects.using(db).alive()
                .annotate(n=Count("enquiries", filter=Q(enquiries__created_at__gte=now - timedelta(days=90))))
                .filter(n__gt=0).order_by("-n")[:6])
    expiring = [v for v in vehicles.filter(Q(insurance_expiry__lte=soon) | Q(inspection_expiry__lte=soon)
                                           | Q(next_service_due__lte=soon))]
    ctx = {
        "kpis": kpis, "charts": charts, "top_destinations": top_dest,
        "recent": enq.order_by("-created_at")[:8],
        "stale": open_qs.filter(stage_changed_at__lt=now - timedelta(days=7)).order_by("stage_changed_at")[:6],
        "expiring_vehicles": expiring[:6],
        "rates": _latest_rates(),
        "stage_list": [{"code": s.value, "label": s.label, "count": stage_counts.get(s.value, 0)} for s in Stage],
    }
    return render(request, "core/ops_dashboard.html", ctx)


def _latest_rates():
    out = []
    for c in Currency.objects.filter(is_active=True, is_base=False):
        r = ExchangeRate.objects.filter(currency=c).order_by("-date").first()
        if r:
            out.append({"code": c.code, "rate": r.rate_to_base, "date": r.date})
    return out


@login_required
def global_search(request):
    term = request.GET.get("q", "").strip()
    results = []
    if len(term) >= 2:
        from apps.core.phone import normalise_phone

        digits = normalise_phone(term) if any(ch.isdigit() for ch in term) else ""
        cq = Q(full_name__icontains=term) | Q(email__icontains=term)
        if digits and len(digits) > 5:
            cq |= Q(phone__contains=digits.lstrip("+")[-9:])
        from apps.contacts.views import visible_clients

        for c in visible_clients(request.user).filter(cq)[:5]:
            results.append({"group": "Clients", "title": c.full_name, "sub": c.phone or c.email, "url": c.get_absolute_url()})
        eq = Q(reference__icontains=term) | Q(client__full_name__icontains=term) | Q(destination_text__icontains=term)
        for e in visible_enquiries(request.user).filter(eq)[:5]:
            results.append({"group": "Enquiries", "title": f"{e.reference} · {e.client.full_name}",
                            "sub": f"{e.get_stage_display()} · {e.interest_label}", "url": e.get_absolute_url()})
        for p in TourPackage.objects.alive().filter(Q(title__icontains=term) | Q(code__icontains=term))[:4]:
            results.append({"group": "Packages", "title": f"{p.code} · {p.title}", "sub": p.duration_label,
                            "url": p.get_absolute_url()})
        for d in Destination.objects.alive().filter(name__icontains=term)[:3]:
            results.append({"group": "Destinations", "title": d.name, "sub": d.get_region_display(),
                            "url": d.get_absolute_url()})
        user = request.user
        if user_can_access_module(user, "sales"):
            from apps.sales.services import bookings_for

            for b in bookings_for(user).filter(Q(reference__icontains=term) | Q(client__full_name__icontains=term))[:4]:
                results.append({"group": "Bookings", "title": f"{b.reference} · {b.client.full_name}",
                                "sub": f"{b.get_status_display()} · {b.title}", "url": b.get_absolute_url()})
        if user_can_access_module(user, "sales"):
            from apps.sales.services import itineraries_for, quotations_for

            for i in itineraries_for(user).filter(Q(title__icontains=term) | Q(enquiry__client__full_name__icontains=term) | Q(enquiry__reference__icontains=term))[:4]:
                results.append({"group": "Itineraries", "title": i.title, "sub": f"{i.enquiry.reference} · {i.day_count} days · {i.get_status_display()}", "url": i.get_absolute_url()})
            for q in quotations_for(user).filter(Q(reference__icontains=term) | Q(enquiry__client__full_name__icontains=term))[:4]:
                results.append({"group": "Quotations", "title": f"{q.reference} · {q.enquiry.client.full_name}", "sub": q.get_status_display(), "url": q.get_absolute_url()})
        low = term.lower()
        pages = []
        for m in modules_for(user):
            for n in m.nav_for(user):
                if not n.args and low in n.label.lower() and n.url != "#" and (n.label, n.url) not in pages:
                    pages.append((n.label, n.url))
                    results.append({"group": "Go to", "title": n.label, "sub": m.name, "url": n.url})
                if len(pages) >= 5:
                    break
        if user_can_access_module(user, "finance"):
            from apps.finance.models import Invoice

            for i in Invoice.objects.filter(Q(number__icontains=term) | Q(client__full_name__icontains=term)).select_related("client")[:4]:
                results.append({"group": "Invoices", "title": f"{i.number} · {i.client.full_name}", "sub": i.display_status,
                                "url": i.get_absolute_url()})
        if user_can_access_module(user, "suppliers"):
            from apps.suppliers.models import Supplier

            for s_ in Supplier.objects.alive().filter(name__icontains=term)[:3]:
                results.append({"group": "Suppliers", "title": s_.name, "sub": s_.get_kind_display(), "url": s_.get_absolute_url()})
        if user_can_access_module(user, "trips"):
            from apps.trips.views import visible_departures

            for d_ in visible_departures(user).filter(Q(reference__icontains=term) | Q(package__title__icontains=term))[:4]:
                results.append({"group": "Departures", "title": f"{d_.reference} · {d_.package.title}",
                                "sub": f"{d_.start_date:%d %b %Y} · {d_.get_stage_display()}", "url": d_.get_absolute_url()})
        fleet = Vehicle.objects.filter(Q(registration_no__icontains=term) | Q(nickname__icontains=term))
        for v in (fleet[:3] if user_can_access_module(request.user, "operations") else []):
            results.append({"group": "Fleet", "title": v.registration_no, "sub": v.get_status_display(),
                            "url": v.get_absolute_url()})
    return render(request, "core/partials/search_results.html", {"results": results, "term": term})


@role_required(roles.ADMIN)
def system_status(request):
    hb = last_heartbeat()
    stale = hb is None or hb < timezone.now() - timedelta(minutes=15)
    states = {s.name: s for s in ScheduleState.objects.all()}
    schedules = [{"s": s, "state": states.get(name)} for name, s in sorted(SCHEDULES.items())]
    ctx = {
        "heartbeat": hb, "stale": stale and settings.JOBS_BACKEND != "sync", "backend": settings.JOBS_BACKEND,
        "failed": Job.objects.filter(status=Job.FAILED)[:30],
        "queued": Job.objects.filter(status=Job.QUEUED).count(),
        "recent": Job.objects.exclude(status=Job.QUEUED)[:20],
        "schedules": schedules,
        "db_engine": settings.DATABASES["default"]["ENGINE"].rsplit(".", 1)[-1],
        "db_name": Path(str(settings.DATABASES["default"]["NAME"])).name,
        "pwa_version": settings.PWA_VERSION,
        "integrations_mode": settings.INTEGRATIONS_MODE,
    }
    return render(request, "core/system_status.html", ctx)


@require_POST
@role_required(roles.ADMIN)
def retry_job(request, pk):
    job = get_object_or_404(Job, pk=pk)
    job.status, job.attempts, job.run_at, job.last_error = Job.QUEUED, 0, timezone.now(), ""
    job.save()
    if settings.JOBS_BACKEND == "sync":
        from .jobs.runner import execute_job

        execute_job(job)
    messages.success(request, f"Job #{job.pk} re-queued.")
    return redirect("core:system_status")


@require_POST
@role_required(roles.ADMIN)
def run_jobs_now(request):
    from .jobs.runner import run_due

    res = run_due(time_limit=30)
    messages.success(request, "Runner busy elsewhere." if res["locked"]
                     else f"Ran {res['ran']} job(s), scheduled {res['scheduled']}.")
    return redirect("core:system_status")


@role_required(roles.ADMIN)
def company(request):
    obj = CompanySetting.load()
    form = CompanyForm(request.POST or None, instance=obj)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Company profile saved.")
        return redirect("core:company")
    return render(request, "components/form_page.html",
                  {"form": form, "title": "Company Profile",
                   "subtitle": "Printed on quotations, invoices, receipts and itineraries.", "cancel_url": "/"})


@module_required("operations")
def standard_terms_page(request):
    """Operations → Standard Terms: the payment and cancellation terms filled into every new itinerary and quotation."""
    from django.http import JsonResponse

    from .models import DEFAULT_TERMS

    if not (request.user.is_superuser or request.user.has_role(roles.ADMIN, roles.COO, roles.OPERATIONS)):
        raise PermissionDenied
    obj = CompanySetting.load()
    modal = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    error = ""
    if request.method == "POST":
        if request.POST.get("action") == "reset":
            text = DEFAULT_TERMS
        else:
            text = (request.POST.get("standard_terms") or "").strip()
        if not text:
            error = "Write the terms first, or use Reset to the built-in default."
        else:
            obj.standard_terms = text[:4000]
            obj.save(update_fields=["standard_terms"])
            messages.success(request, "Standard terms saved. New itineraries and quotations start with them.")
            return JsonResponse({"ok": True}) if modal else redirect("core:standard_terms")
    if modal:
        return render(request, "core/standard_terms_modal.html", {"terms": request.POST.get("standard_terms", obj.standard_terms), "error": error},
                      status=422 if error else 200)
    return render(request, "core/standard_terms.html", {"terms": obj.standard_terms, "is_default": obj.standard_terms.strip() == DEFAULT_TERMS.strip()})


@role_required(roles.ADMIN)
def security_settings(request):
    """Administration → Security Settings: one button to turn two-factor sign-in on or off for everyone who must use it."""
    from django.conf import settings as dj

    from apps.accounts import twofactor
    from apps.accounts.models import User
    from apps.system.services import audit

    forced = bool(dj.TWO_FACTOR_ENABLED)
    if request.method == "POST":
        if forced:
            messages.error(request, "Two-factor is forced on by the server settings, so it cannot be switched off here.")
        else:
            on = request.POST.get("action") == "on"
            twofactor.set_feature(on)
            audit(request.user, "2fa.feature_on" if on else "2fa.feature_off", None, detail="Two-factor sign-in " + ("turned on" if on else "turned off"), request=request, label="Two-factor sign-in")
            messages.success(request, "Two-factor sign-in is now ON. People in the required roles will be asked to set it up at their next page." if on
                             else "Two-factor sign-in is now OFF.")
        return redirect("core:security_settings")
    must = [u for u in User.objects.filter(is_active=True).select_related("employee") if u.has_role(*dj.TWO_FACTOR_REQUIRED_ROLES)]
    return render(request, "core/security_settings.html", {
        "on": twofactor.feature_on(), "forced": forced, "roles": dj.TWO_FACTOR_REQUIRED_ROLES,
        "total": len(must), "enrolled": [u for u in must if u.totp_enabled], "pending": [u for u in must if not u.totp_enabled]})


PUBLIC_MEDIA = {"packages", "destinations"}   # catalogue photos shown on the public site


def protected_media(request, path):
    """Uploaded files are only served to signed-in staff (catalogue photos are public); private folders need extra roles."""
    if path.split("/", 1)[0] not in PUBLIC_MEDIA and not request.user.is_authenticated:
        from django.contrib.auth.views import redirect_to_login

        return redirect_to_login(request.get_full_path())
    root = Path(settings.MEDIA_ROOT).resolve()
    full = (root / path).resolve()
    if root not in full.parents or not full.is_file():
        raise Http404
    private = path.split("/", 1)[0] in {"passports", "contracts", "payment-proofs", "payslips"}
    if private and not request.user.has_role(roles.ADMIN, roles.FINANCE, roles.SALES_MANAGER, roles.RESERVATIONS,
                                  roles.FRONTDESK_MANAGER):
        raise PermissionDenied
    ctype, _ = mimetypes.guess_type(str(full))
    resp = FileResponse(open(full, "rb"), content_type=ctype or "application/octet-stream")
    resp["Cache-Control"] = "private, max-age=3600"
    resp["X-Content-Type-Options"] = "nosniff"
    return resp


# ---- branded error pages
def error_400(request, exception=None):
    return render(request, "errors/error.html", {"code": 400, "title": "Bad request",
                  "text": "Something in that request didn't look right."}, status=400)


def error_403(request, exception=None):
    return render(request, "errors/error.html", {"code": 403, "title": "Not allowed",
                  "text": "Your role doesn't have access to this page."}, status=403)


def error_404(request, exception=None):
    return render(request, "errors/error.html", {"code": 404, "title": "Off the map",
                  "text": "We couldn't find that page. It may have moved or been removed."}, status=404)


def error_500(request):
    return render(request, "errors/error.html", {"code": 500, "title": "Something went wrong",
                  "text": "The team has been notified. Please try again in a moment."}, status=500)


def charts_json(data):
    return json.dumps(data)
