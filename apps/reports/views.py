from datetime import date, timedelta
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.core.pdf import render_pdf
from apps.core.models import CompanySetting

from . import export, registry
from .models import ScheduledReport


def _dates(request):
    today = timezone.localdate()
    try:
        end = date.fromisoformat(request.GET.get("to") or "")
    except ValueError:
        end = today
    try:
        start = date.fromisoformat(request.GET.get("from") or "")
    except ValueError:
        start = end - timedelta(days=30)
    return start, end


@login_required
def index(request):
    mine = {s.report_key: s for s in ScheduledReport.objects.filter(user=request.user)}
    groups = {}
    available = registry.available(request.user)
    for r in available:
        groups.setdefault(r.module, []).append((r, mine.get(r.key)))
    today = timezone.localdate()
    scheduled = [{"s": s, "rep": registry.REPORTS[s.report_key]} for s in mine.values() if s.report_key in registry.REPORTS]
    return render(request, "reports/index.html", {
        "groups": groups, "available": available, "scheduled": scheduled, "freqs": ScheduledReport.FREQ,
        "start": today - timedelta(days=30), "end": today, "first_key": available[0].key if available else ""})


@login_required
def view(request, key):
    if key not in registry.REPORTS:
        raise PermissionDenied
    start, end = _dates(request)
    try:
        rep, cols, rows = registry.run(key, request.user, start, end)
    except PermissionError:
        raise PermissionDenied
    fmt = request.GET.get("format")
    name = f"{rep.key}-{end:%Y%m%d}"
    if fmt == "csv":
        return export.to_csv(name, cols, rows)
    if fmt == "xlsx":
        return export.to_xlsx(name, cols, rows)
    rows = [[f"{v:,.2f}" if isinstance(v, Decimal) else ("" if v is None else v) for v in r] for r in rows]
    ctx = {"rep": rep, "cols": cols, "rows": rows, "start": start, "end": end, "company": CompanySetting.load(), "generated": timezone.now(), "user": request.user}
    if fmt == "pdf":
        return render_pdf(request, "reports/pdf.html", ctx, name + ".pdf", fallback_url=request.path)
    ctx["schedule"] = ScheduledReport.objects.filter(user=request.user, report_key=key).first()
    return render(request, "reports/view.html", ctx)


@login_required
@require_POST
def schedule(request, key):
    if key not in registry.REPORTS or key not in {r.key for r in registry.available(request.user)}:
        raise PermissionDenied
    back = "reports:index" if request.POST.get("from_hub") else None
    if request.POST.get("action") == "remove":
        ScheduledReport.objects.filter(user=request.user, report_key=key).delete()
        messages.success(request, "Schedule removed.")
    elif request.POST.get("action") in ("pause", "resume"):
        ScheduledReport.objects.filter(user=request.user, report_key=key).update(active=request.POST["action"] == "resume")
        messages.success(request, "Schedule paused." if request.POST["action"] == "pause" else "Schedule resumed.")
    else:
        freq = request.POST.get("frequency") if request.POST.get("frequency") in dict(ScheduledReport.FREQ) else "weekly"
        try:
            days = max(1, min(int(request.POST.get("range_days") or 30), 365))
        except ValueError:
            days = 30
        ScheduledReport.objects.update_or_create(user=request.user, report_key=key, defaults={"frequency": freq, "range_days": days, "active": True})
        messages.success(request, "Scheduled. It will be e-mailed to you as an Excel file.")
    return redirect(back) if back else redirect("reports:view", key=key)
