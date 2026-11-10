import json

from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST

from apps.accounts import roles as R
from apps.accounts.permissions import is_scoped_agent, module_required
from apps.leads.views import visible_enquiries

from . import executive, matching


def _execs(user):
    return user.is_superuser or user.has_role(*R.ALL_ACCESS)


@module_required("intelligence", "me")
def command_centre(request):
    if not _execs(request.user):
        raise PermissionDenied
    c = executive.centre()
    return render(request, "insights/centre.html", {"c": c, "items": executive.attention(c)})


@require_POST
@module_required("me")
def ask(request):
    if not _execs(request.user):
        raise PermissionDenied
    try:
        q = (json.loads(request.body or b"{}").get("question") or "").strip()
    except ValueError:
        q = ""
    if not q:
        return JsonResponse({"error": "Ask a question."}, status=400)
    return JsonResponse(executive.ask(request.user, q))


@module_required("intelligence", "sales")
def customer_care(request):
    qs = visible_enquiries(request.user)
    flags = matching.care_flags(qs)
    return render(request, "insights/care.html", {"flags": flags, "scoped": is_scoped_agent(request.user), "danger": sum(1 for f in flags if f["level"] == "danger"), "watch": sum(1 for f in flags if f["level"] != "danger"), "checked": qs.count()})


@module_required("intelligence", "sales")
def sales_insights(request):
    from . import sales

    if not (request.user.is_superuser or request.user.has_role(*R.ALL_ACCESS, R.SALES_MANAGER)):
        raise PermissionDenied
    return render(request, "insights/sales.html", {"m": sales.team_month(), "stalled": sales.stalled(), "stalled_sum": sales.stalled_summary(), "funnel": sales.funnel_30()})


@module_required("intelligence", "sales", "marketing", "me")
def research(request):
    from . import research as rs

    if not (request.user.is_superuser or request.user.has_role(*R.ALL_ACCESS, R.SALES_MANAGER, R.MARKETING)):
        raise PermissionDenied
    days = request.GET.get("days")
    days = int(days) if days in ("30", "60", "90", "180") else 90
    return render(request, "insights/research.html", {"d": rs.demand(days), "days": days})
