"""Team Performance: the weekly ranking every sales agent can see (no client details, no pay)."""
from datetime import timedelta
from decimal import Decimal

from django.db.models import Count
from django.utils import timezone

from apps.leads.models import LeadActivity


def week_bounds(offset=0):
    today = timezone.localdate()
    monday = today - timedelta(days=today.weekday()) - timedelta(weeks=offset)
    return monday, monday + timedelta(days=6)


def table(offset=0):
    """Rows ranked by month-to-date % of target (then revenue). Week columns are for the chosen week; target columns are for that week's month."""
    from apps.people.models import Employee
    from apps.sales import analytics
    from apps.sales.models import Booking, BookingStatus, LeadItinerary, Quotation

    start, end = week_bounds(offset)
    agents = list(Employee.objects.filter(is_sales_agent=True, is_active=True))
    wk = {r["agent"].pk: r for r in analytics.agent_rows(start, end, agents)}
    mo = {r["agent"].pk: r for r in analytics.agent_rows(end.replace(day=1), end, agents)}
    ids = [a.pk for a in agents]
    its = dict(LeadItinerary.objects.filter(enquiry__assigned_agent_id__in=ids, sent_at__date__range=(start, end)).values_list("enquiry__assigned_agent").annotate(n=Count("pk")))
    deposits = dict(Booking.objects.filter(agent_id__in=ids, booked_at__date__range=(start, end)).exclude(status=BookingStatus.CANCELLED).values_list("agent").annotate(n=Count("pk")))
    rows = []
    for a in agents:
        w, m = wk[a.pk], mo[a.pk]
        t = m["target"]
        pct = round(100 * m["month_revenue"] / t.revenue_target) if t and t.revenue_target else None
        rows.append({"agent": a, "calls": w["calls"], "conversations": w["connected"], "quotes": w["quotes"], "itineraries": its.get(a.pk, 0), "bookings": deposits.get(a.pk, 0),
                     "won": w["won"], "month_revenue": m["month_revenue"], "target": t.revenue_target if t and t.revenue_target else None, "pct": pct})
    rows.sort(key=lambda r: (-(r["pct"] if r["pct"] is not None else -1), -r["month_revenue"]))
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    totals = {k: sum(r[k] for r in rows) for k in ("calls", "conversations", "quotes", "itineraries", "bookings")}
    totals["revenue"] = sum((r["month_revenue"] for r in rows), Decimal("0"))
    return {"rows": rows, "totals": totals, "start": start, "end": end, "offset": offset}
