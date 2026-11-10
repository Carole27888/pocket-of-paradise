from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts import roles as R
from apps.accounts.permissions import module_required

from .models import CoachMessage, CoachSettings
from . import services


def _can_configure(user):
    return user.is_superuser or user.has_role(R.SALES_MANAGER, R.COO, R.DIRECTOR, R.ADMIN)


def _sections(f):
    """Today's briefing as sections of linked rows, read live from the system so every name opens the lead."""
    def hm(m):
        return f"{m // 60}h {m % 60}m" if m < 2880 else f"{m // 1440} days"

    out = [
        {"key": "call", "title": "New leads to call first", "icon": "phone", "tone": "danger", "empty": "No new lead is waiting for a first call.",
         "items": [{"title": r["client"], "sub": r["interest"], "meta": f"waiting {hm(r['minutes'])}", "url": r["url"]} for r in f["uncontacted"]]},
        {"key": "followups", "title": "Follow-ups due", "icon": "check-square", "tone": "warning", "empty": "Nothing is due today.",
         "items": [{"title": r["title"], "sub": r["client"], "meta": r["due"], "late": r["late"], "url": r["url"]} for r in f["followups"]]},
        {"key": "quotes", "title": "Quotations with no reply", "icon": "file-text", "tone": "info", "empty": "Every sent quotation has had a response.",
         "items": [{"title": r["client"], "sub": f"{r['quote']} · {r['total']}", "meta": f"sent {r['days']} days ago", "url": r["url"]} for r in f["quotes"]]},
        {"key": "pay", "title": "Payments to chase", "icon": "wallet", "tone": "danger", "empty": "No payment needs chasing.",
         "items": [{"title": r["client"], "sub": r["ref"], "meta": r["what"], "url": r["url"]} for r in f["bookings"]]},
        {"key": "quiet", "title": "Leads going quiet", "icon": "clock", "tone": "warning", "empty": "",
         "items": [{"title": r["client"], "sub": f"{r['interest']} · {r['stage']}", "meta": f"no contact for {r['days']} days", "url": r["url"]} for r in f["stale"]]},
        {"key": "trips", "title": "Travelling this week", "icon": "route", "tone": "success", "empty": "",
         "items": [{"title": r["client"], "sub": r["ref"], "meta": f"on {r['date']}", "url": ""} for r in f["departures"]]},
    ]
    for sec in out:
        sec["shown"], sec["more"] = sec["items"][:5], sec["items"][5:]
    return out


@module_required("sales")
def inbox(request):
    from . import services as coach

    emp = getattr(request.user, "employee", None)
    if emp is None:
        return render(request, "coach/inbox.html", {"emp": None})
    msgs = list(CoachMessage.objects.filter(employee=emp).select_related("enquiry")[:60])
    unread = [m for m in msgs if m.read_at is None]
    CoachMessage.objects.filter(pk__in=[m.pk for m in unread]).update(read_at=timezone.now())
    brief = sections = None
    if emp.is_sales_agent:
        brief = coach.briefing_facts(emp)
        sections = _sections(brief)
    today = timezone.localdate()
    # today's briefing is shown as the structured view above; everything else stays as messages
    others = [m for m in msgs if not (m.kind == CoachMessage.BRIEFING and m.day == today)]
    ctx = {"emp": emp, "msgs": others, "unread_ids": {m.pk for m in unread}, "can_configure": _can_configure(request.user), "brief": brief, "sections": sections,
           "today": today, "first_name": (emp.full_name or "").split(" ")[0]}
    if brief:
        ctx["stats"] = {"call": len(brief["uncontacted"]), "followups": len(brief["followups"]), "late": sum(1 for r in brief["followups"] if r["late"]),
                        "quotes": len(brief["quotes"]), "pay": len(brief["bookings"])}
    return render(request, "coach/inbox.html", ctx)


@require_POST
@module_required("sales")
def reply(request, pk):
    m = get_object_or_404(CoachMessage, pk=pk, employee__user=request.user)
    text = request.POST.get("reply", "").strip()[:500]
    if text:
        m.reply, m.replied_at = text, timezone.now()
        m.save(update_fields=["reply", "replied_at"])
        if m.enquiry_id:
            from apps.leads.services import log_activity

            log_activity(m.enquiry, "note", f"Agent's reason: {text}", request.user)
        messages.success(request, "Saved. Thank you.")
    return redirect("coach:inbox")


@module_required("sales")
def settings_view(request):
    if not _can_configure(request.user):
        raise PermissionDenied
    cfg = CoachSettings.load()
    if request.method == "POST":
        for f in ("briefing_on", "nudges_on", "end_of_day_on", "weekly_on", "digest_on", "exec_briefing_on", "no_sundays", "send_email", "send_whatsapp", "use_ai"):
            setattr(cfg, f, bool(request.POST.get(f)))
        for f in ("exec_briefing_at", "briefing_at", "end_of_day_at", "digest_at", "weekly_at", "work_starts", "work_ends"):
            if request.POST.get(f):
                setattr(cfg, f, request.POST[f])
        for f, lo, hi in (("uncontacted_minutes", 5, 1440), ("stale_lead_days", 1, 60), ("stale_quote_days", 1, 60), ("manager_alert_hours", 1, 72), ("max_nudges_per_day", 1, 40)):
            try:
                setattr(cfg, f, max(lo, min(int(request.POST.get(f, getattr(cfg, f))), hi)))
            except ValueError:
                pass
        cfg.updated_by = request.user
        cfg.save()
        messages.success(request, "Assistant settings saved.")
        return redirect("coach:settings")
    toggles = [("briefing_on", "Morning briefing at first sign-in of the day"), ("nudges_on", "Nudges about pending work"), ("end_of_day_on", "End-of-day report"), ("weekly_on", "Monday weekly summary"),
               ("digest_on", "Daily digest to managers"), ("exec_briefing_on", "Morning briefing to the CEO and COO"), ("no_sundays", "No messages on Sundays"),
               ("send_email", "Also send by e-mail"), ("send_whatsapp", "Also send briefings and reports by WhatsApp"), ("use_ai", "Word messages with AI when a key is saved")]
    return render(request, "coach/settings.html", {"cfg": cfg, "toggles": toggles})


@module_required("sales")
def team_performance(request):
    from apps.core.models import CompanySetting
    from apps.core.pdf import render_pdf

    from . import team

    try:
        offset = max(0, min(int(request.GET.get("week", 0)), 52))
    except ValueError:
        offset = 0
    data = team.table(offset)
    mine = getattr(request.user, "employee", None)
    ctx = {**data, "me_id": mine.pk if mine else None, "company": CompanySetting.load(), "generated": timezone.now()}
    if request.GET.get("format") == "pdf":
        return render_pdf(request, "coach/team_pdf.html", ctx, f"team-performance-{data['start']:%Y%m%d}.pdf", fallback_url=request.path)
    return render(request, "coach/team.html", ctx)
