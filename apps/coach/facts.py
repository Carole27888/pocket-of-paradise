"""What the assistant knows about an agent's day, read straight from the system's data (nothing is invented)."""
from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, Max, Q, Sum, F
from django.db.models.functions import Coalesce
from django.utils import timezone

from apps.leads.models import Enquiry, LeadActivity, LeadAssignment, OPEN_STAGES, Stage


def _row(e, now, since=None) -> dict:
    row = {"id": e.pk, "client": e.client.full_name, "interest": e.interest_label, "stage": e.get_stage_display(), "url": e.get_absolute_url(), "ref": e.reference}
    if since:
        row["minutes"] = int((now - since).total_seconds() // 60)
        row["days"] = (now - since).days
    return row


def open_leads(emp):
    return Enquiry.objects.filter(assigned_agent=emp, stage__in=OPEN_STAGES).select_related("client", "package", "destination")


def uncontacted(emp, min_minutes=0, now=None):
    now = now or timezone.now()
    qs = (open_leads(emp).filter(last_contact_at__isnull=True, stage=Stage.ENQUIRY)
          .annotate(clock=Coalesce(Max("assignments__assigned_at"), F("created_at"))).filter(clock__lte=now - timedelta(minutes=min_minutes)).order_by("clock"))
    return [_row(e, now, e.clock) for e in qs[:20]]


def stale_leads(emp, days, now=None):
    now = now or timezone.now()
    qs = open_leads(emp).exclude(stage=Stage.ENQUIRY).filter(last_contact_at__lt=now - timedelta(days=days)).order_by("last_contact_at")
    return [_row(e, now, e.last_contact_at) for e in qs[:20]]


def stale_quotes(emp, days, now=None):
    from apps.sales.models import Quotation, QuotationStatus

    now = now or timezone.now()
    out = []
    for q in Quotation.objects.filter(agent=emp, status=QuotationStatus.SENT, sent_at__lt=now - timedelta(days=days)).select_related("enquiry__client", "enquiry__package"):
        last = q.enquiry.last_contact_at
        if last is None or last < now - timedelta(days=days):
            r = _row(q.enquiry, now, last or q.sent_at)
            r.update({"quote": q.reference, "total": f"{q.currency} {q.total:,.0f}"})
            out.append(r)
    return out[:20]


def followups(emp, now=None):
    from apps.sales.models import FollowUp

    now = now or timezone.now()
    qs = FollowUp.objects.filter(agent=emp, done_at__isnull=True, due_at__lte=now.replace(hour=23, minute=59)).select_related("enquiry__client").order_by("due_at")
    return [{"title": f.title, "client": f.enquiry.client.full_name, "due": timezone.localtime(f.due_at).strftime("%d %b %H:%M"), "late": f.due_at < now, "url": f.enquiry.get_absolute_url()} for f in qs[:15]]


def bookings_to_chase(emp, now=None):
    """Bookings awaiting a deposit, or with a balance and travel inside three weeks."""
    from apps.sales.models import Booking, BookingStatus

    today = (now or timezone.now()).date()
    out = []
    for b in Booking.objects.filter(agent=emp).exclude(status__in=[BookingStatus.CANCELLED, BookingStatus.COMPLETED]).select_related("client"):
        if b.status == BookingStatus.PENDING:
            out.append({"client": b.client.full_name, "ref": b.reference, "what": f"deposit of {b.currency} {b.deposit_amount:,.0f} not yet paid", "url": b.get_absolute_url()})
        elif b.balance > 0 and b.travel_start and 0 <= (b.travel_start - today).days <= 21:
            out.append({"client": b.client.full_name, "ref": b.reference, "what": f"balance {b.currency} {b.balance:,.0f}, travelling {b.travel_start:%d %b}", "url": b.get_absolute_url()})
    return out[:15]


def departures_soon(emp, today):
    from apps.sales.models import Booking, BookingStatus

    return [{"client": b.client.full_name, "ref": b.reference, "date": b.travel_start.strftime("%d %b")} for b in
            Booking.objects.filter(agent=emp, travel_start__range=(today, today + timedelta(days=7))).exclude(status=BookingStatus.CANCELLED).select_related("client")[:10]]


def todos(emp):
    from apps.system.models import TodoItem

    if not emp.user_id:
        return []
    return list(TodoItem.objects.filter(user_id=emp.user_id, done_at__isnull=True).values_list("text", flat=True)[:10])


def activity(emp, start, end) -> dict:
    from apps.sales.models import Booking, CallLog, Quotation

    acts = dict(LeadActivity.objects.filter(enquiry__assigned_agent=emp, timestamp__gte=start, timestamp__lt=end).values_list("activity_type").annotate(n=Count("pk")))
    calls = CallLog.objects.filter(agent=emp, called_at__gte=start, called_at__lt=end)
    return {"calls": calls.count(), "connected": calls.filter(outcome__in=CallLog.CONNECTED).count(), "whatsapp": acts.get("whatsapp", 0), "emails": acts.get("email", 0),
            "quotes": Quotation.objects.filter(agent=emp, sent_at__gte=start, sent_at__lt=end).count(),
            "new_leads": LeadAssignment.objects.filter(agent=emp, assigned_at__gte=start, assigned_at__lt=end).count(),
            "stage_moves": acts.get("stage", 0), "bookings": Booking.objects.filter(agent=emp, booked_at__gte=start, booked_at__lt=end).count()}


def month(emp, today) -> dict:
    """Target progress and rank for the month, from the same numbers as Sales → Targets."""
    from apps.people.models import Employee
    from apps.sales import analytics

    start = today.replace(day=1)
    rows = sorted(analytics.agent_rows(start, today, Employee.objects.filter(is_sales_agent=True, is_active=True)), key=lambda r: -r["month_revenue"])
    me = next((r for r in rows if r["agent"].pk == emp.pk), None)
    if not me:
        return {"rank": None, "of": len(rows), "revenue": "0", "target": None, "pct": None, "bookings": 0}
    t = me["target"]
    rank = rows.index(me) + 1
    out = {"rank": rank, "of": len(rows), "revenue": str(me["month_revenue"]), "bookings": me["month_bookings"], "target": str(t.revenue_target) if t and t.revenue_target else None,
           "pct": round(100 * me["month_revenue"] / t.revenue_target) if t and t.revenue_target else None, "gap": None}
    if t and t.revenue_target:
        out["gap"] = str(max(t.revenue_target - me["month_revenue"], Decimal("0")))
    if rank > 1:
        out["behind"] = {"name": rows[rank - 2]["agent"].full_name, "gap": str(rows[rank - 2]["month_revenue"] - me["month_revenue"])}
    return out
