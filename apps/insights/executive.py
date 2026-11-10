"""Command Centre numbers for the CEO, COO and Admin, plus "Ask the business": answers come only from these live figures."""
from datetime import timedelta
from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from apps.leads.models import OPEN_STAGES, Enquiry, Stage


def centre() -> dict:
    from apps.finance.models import Invoice
    from apps.sales import services
    from apps.sales.models import Booking, BookingStatus, FollowUp, SalesTarget
    from apps.trips.models import Departure

    now, today = timezone.now(), timezone.localdate()
    ms = today.replace(day=1)
    bookings = Booking.objects.exclude(status=BookingStatus.CANCELLED).filter(booked_at__date__gte=ms)
    revenue = services.sum_kes(bookings)
    target = SalesTarget.objects.filter(year=today.year, month=today.month).aggregate(t=Sum("revenue_target"))["t"] or Decimal("0")
    open_qs = Enquiry.objects.filter(stage__in=OPEN_STAGES)
    out = {"revenue": revenue, "target": target, "revenue_pct": round(100 * revenue / target) if target else None, "bookings": bookings.count(),
           "open_leads": open_qs.count(), "pipeline": open_qs.aggregate(v=Sum("deal_value"))["v"] or Decimal("0"),
           "waiting": open_qs.filter(assigned_agent__isnull=True).count(), "overdue_followups": FollowUp.objects.filter(done_at__isnull=True, due_at__lt=now).count(),
           "departures_week": Departure.objects.filter(start_date__range=(today, today + timedelta(days=7))).exclude(stage="cancelled").count(),
           "overdue_invoices": Invoice.objects.filter(status__in=["sent", "part_paid"], due_date__lt=today).count(),
           "sla_breaches": services.sla_breaches().count(), "going_cold": services.going_cold().count(),
           "awaiting_deposit": Booking.objects.filter(status=BookingStatus.PENDING, booked_at__lt=now - timedelta(days=3)).count(), "lost_month": Enquiry.objects.filter(stage=Stage.LOST, stage_changed_at__date__gte=ms).count()}
    return out


def attention(c=None) -> list:
    """What needs a person today, most urgent first, each with the evidence and where to go."""
    c = c or centre()
    items = []

    def add(n, text, url, level):
        if n:
            items.append({"n": n, "text": text, "url": url, "level": level})
    add(c["waiting"], "leads are waiting for an agent at the Front Desk", "/frontdesk/queues/?state=waiting&period=30d", "danger")
    add(c["sla_breaches"], "leads have gone past the response time with no contact", "/sales/", "danger")
    add(c["overdue_followups"], "follow-ups are overdue", "/sales/followups/", "warning")
    add(c["going_cold"], "quoted or hot leads are going cold", "/sales/", "warning")
    add(c["awaiting_deposit"], "bookings have waited 3+ days for a deposit", "/sales/bookings/", "warning")
    add(c["overdue_invoices"], "invoices are overdue", "/finance/", "warning")
    add(c["departures_week"], "departures this week need to be ready", "/trips/", "info")
    return items


def ask(user, question: str) -> dict:
    """Answer from live figures only. With an AI key it phrases the answer; without one it states the relevant figures."""
    c = centre()
    facts = {k: (str(v) if isinstance(v, Decimal) else v) for k, v in c.items()}
    facts["attention"] = [f"{i['n']} {i['text']}" for i in attention(c)]
    try:
        from apps.ict import ai

        if ai.configured():
            system = ("You are the business analyst for a Kenyan safari company. Answer the question using ONLY the figures provided. If the figures do not answer it, say what is missing. "
                      "Be brief (under 100 words), say how confident you are, and cite the figures you used.")
            resp = ai.complete(system, [{"role": "user", "content": f"Figures: {facts}\n\nQuestion: {question[:400]}"}], max_tokens=300, feature="insights")
            text = ai.text_of(resp)
            if text:
                return {"answer": text, "ai": True, "facts": facts}
    except Exception:
        pass
    lines = [f"Revenue this month: KES {c['revenue']:,.0f}" + (f" ({c['revenue_pct']}% of the KES {c['target']:,.0f} target)" if c.get("revenue_pct") is not None else ""),
             f"Open pipeline: {c['open_leads']} leads worth KES {c['pipeline']:,.0f}", *facts["attention"]]
    return {"answer": "The AI assistant isn't switched on, so here are the live figures:\n" + "\n".join(f"• {l}" for l in lines), "ai": False, "facts": facts}
