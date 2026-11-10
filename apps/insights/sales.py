"""Sales manager's view: pipeline health, who needs support and why, stalled deals, and who is likely to miss target.
Everything is computed from records and each judgement shows its evidence. Low activity is a support need, never an accusation."""
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from apps.leads.models import OPEN_STAGES, Enquiry
from apps.sales import analytics


def team_month(today=None) -> dict:
    today = today or timezone.localdate()
    start = today.replace(day=1)
    rows = analytics.agent_rows(start, today)
    days_in = (today.replace(day=28) + timedelta(days=4)).replace(day=1) - start
    pace = today.day / days_in.days  # share of the month gone
    out = []
    for r in rows:
        t = r["target"]
        pct = round(100 * r["month_revenue"] / t.revenue_target) if t and t.revenue_target else None
        projected = round(100 * float(r["month_revenue"]) / pace / float(t.revenue_target)) if t and t.revenue_target and pace else None
        needs, evidence = [], []
        if r["breaches"]:
            needs.append("Slow first response"); evidence.append(f"{r['breaches']} new lead(s) past the response target")
        if r["overdue"]:
            needs.append("Follow-ups overdue"); evidence.append(f"{r['overdue']} overdue follow-up(s)")
        if r["cold"]:
            needs.append("Leads going cold"); evidence.append(f"{r['cold']} open lead(s) with no contact for days")
        if r["assigned"] >= 5 and r["quotes"] == 0:
            needs.append("No quotes sent"); evidence.append(f"{r['assigned']} leads this month, 0 quotations")
        if r["calls"] < today.day and r["open"] >= 5:
            needs.append("Low call activity"); evidence.append(f"{r['calls']} calls in {today.day} days with {r['open']} open leads")
        risk = "high" if projected is not None and projected < 60 else "watch" if projected is not None and projected < 90 else "ok" if projected is not None else "no target"
        confidence = "good" if today.day >= 10 and pct is not None else "low (early in the month)" if projected is not None else "n/a"
        out.append({**r, "pct": pct, "projected": projected, "risk": risk, "confidence": confidence, "needs": list(zip(needs, evidence))})
    out.sort(key=lambda x: ({"high": 0, "watch": 1, "ok": 2, "no target": 3}[x["risk"]], -len(x["needs"])))
    return {"rows": out, "start": start, "pace": round(pace * 100), "totals": analytics.totals(rows)}


def stalled(days: int = 10, limit: int = 15) -> list:
    """Open deals whose stage has not moved for `days`, biggest value first."""
    cutoff = timezone.now() - timedelta(days=days)
    qs = (Enquiry.objects.filter(stage__in=OPEN_STAGES, stage_changed_at__lt=cutoff).select_related("client", "assigned_agent", "destination")
          .order_by("-deal_value", "stage_changed_at")[:limit])
    return [{"e": e, "days": (timezone.now() - e.stage_changed_at).days} for e in qs]


def stalled_summary(days: int = 10) -> dict:
    """The whole stalled pile (not just the 15 listed): how many, how much money, and where it is stuck."""
    from django.db.models import Count, Sum

    cutoff = timezone.now() - timedelta(days=days)
    qs = Enquiry.objects.filter(stage__in=OPEN_STAGES, stage_changed_at__lt=cutoff)
    by_stage = [{"label": dict(Enquiry._meta.get_field("stage").choices).get(r["stage"], r["stage"]), "n": r["n"], "value": r["v"] or 0}
                for r in qs.values("stage").annotate(n=Count("pk"), v=Sum("deal_value")).order_by("-n")]
    by_agent = [{"label": (r["assigned_agent__full_name"] or "Unassigned"), "n": r["n"], "value": r["v"] or 0}
                for r in qs.values("assigned_agent__full_name").annotate(n=Count("pk"), v=Sum("deal_value")).order_by("-n")[:6]]
    return {"count": qs.count(), "value": qs.aggregate(v=Sum("deal_value"))["v"] or 0, "by_stage": by_stage,
            "by_agent": by_agent, "top_stage": by_stage[0]["n"] if by_stage else 1, "top_agent": by_agent[0]["n"] if by_agent else 1}


def funnel_30(today=None):
    today = today or timezone.localdate()
    return analytics.funnel(Enquiry.objects.all(), today - timedelta(days=30), today)
