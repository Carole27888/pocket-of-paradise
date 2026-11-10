"""Read-only sales numbers: team overview, per-agent performance, funnel, sources, targets."""
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Count, Exists, F, OuterRef, Q, Subquery, Sum
from django.db.models.functions import TruncMonth
from django.utils import timezone

from apps.leads.models import OPEN_STAGES, Enquiry, LeadActivity, LeadAssignment, Source, Stage
from apps.people.models import Employee

from . import services
from .models import Booking, BookingStatus, CallLog, FollowUp, Quotation, QuotationStatus, SalesTarget

RANGES = [("today", "Today"), ("week", "This week"), ("month", "This month"), ("quarter", "This quarter"),
          ("year", "This year")]


def period(key: str):
    """(start_date, end_date, label) for a named range, inclusive."""
    today = timezone.localdate()
    key = key if key in dict(RANGES) else "month"
    if key == "today":
        start = today
    elif key == "week":
        start = today - timedelta(days=today.weekday())
    elif key == "quarter":
        start = date(today.year, 3 * ((today.month - 1) // 3) + 1, 1)
    elif key == "year":
        start = date(today.year, 1, 1)
    else:
        start = today.replace(day=1)
    return start, today, key


def _bookings(start, end):
    return Booking.objects.exclude(status=BookingStatus.CANCELLED).filter(
        booked_at__date__gte=start, booked_at__date__lte=end)


def agent_rows(start, end, agents=None):
    """One dict of numbers per sales agent for [start, end]; every number is a single grouped query."""
    agents = list(agents if agents is not None else Employee.objects.filter(is_sales_agent=True, is_active=True))
    ids = [a.pk for a in agents]
    open_qs = Enquiry.objects.filter(stage__in=OPEN_STAGES, assigned_agent_id__in=ids)
    open_by = {r["assigned_agent"]: r for r in open_qs.values("assigned_agent").annotate(
        n=Count("pk"), value=Sum("deal_value"))}
    weighted = defaultdict(Decimal)
    for r in open_qs.values("assigned_agent", "stage").annotate(value=Sum("deal_value")):
        weighted[r["assigned_agent"]] += Decimal(r["value"] or 0) * services.STAGE_WEIGHT.get(r["stage"], 0) / 100
    assigned = dict(LeadAssignment.objects.filter(agent_id__in=ids, assigned_at__date__gte=start,
                                                  assigned_at__date__lte=end)
                    .values_list("agent").annotate(n=Count("enquiry", distinct=True)))
    won_qs = _bookings(start, end).filter(agent_id__in=ids)
    won = {r["agent"]: r for r in won_qs.values("agent").annotate(
        n=Count("pk"), kes=Sum(F("total_amount") * F("fx_rate")))}
    quotes = dict(Quotation.objects.filter(agent_id__in=ids, sent_at__date__gte=start, sent_at__date__lte=end)
                  .values_list("agent").annotate(n=Count("pk")))
    calls_all = dict(CallLog.objects.filter(agent_id__in=ids, called_at__date__gte=start, called_at__date__lte=end)
                     .values_list("agent").annotate(n=Count("pk")))
    calls_conn = dict(CallLog.objects.filter(agent_id__in=ids, called_at__date__gte=start, called_at__date__lte=end,
                                             outcome__in=CallLog.CONNECTED).values_list("agent").annotate(n=Count("pk")))
    overdue = dict(FollowUp.objects.filter(agent_id__in=ids, done_at__isnull=True, due_at__lt=timezone.now())
                   .values_list("agent").annotate(n=Count("pk")))
    lost = dict(Enquiry.objects.filter(assigned_agent_id__in=ids, stage=Stage.LOST, stage_changed_at__date__gte=start,
                                       stage_changed_at__date__lte=end).values_list("assigned_agent")
                .annotate(n=Count("pk")))
    breaches = dict(services.sla_breaches(open_qs).values_list("assigned_agent").annotate(n=Count("pk")))
    cold = dict(services.going_cold(open_qs).values_list("assigned_agent").annotate(n=Count("pk")))
    first = (LeadActivity.objects.filter(enquiry=OuterRef("enquiry"), activity_type__in=LeadActivity.CONTACT_TYPES,
                                         timestamp__gte=OuterRef("assigned_at")).order_by("timestamp").values("timestamp")[:1])
    resp = defaultdict(list)
    for a_id, assigned_at, ft in (LeadAssignment.objects.filter(agent_id__in=ids, assigned_at__date__gte=start,
                                                                assigned_at__date__lte=end)
                                  .annotate(ft=Subquery(first)).values_list("agent", "assigned_at", "ft")):
        if ft:
            resp[a_id].append((ft - assigned_at).total_seconds() / 60)
    targets = {t.agent_id: t for t in SalesTarget.objects.filter(
        agent_id__in=ids, year=end.year, month=end.month)}
    month_start = end.replace(day=1)
    month_rev = {r["agent"]: r["kes"] for r in _bookings(month_start, end).filter(agent_id__in=ids)
                 .values("agent").annotate(kes=Sum(F("total_amount") * F("fx_rate")))}
    month_bk = dict(_bookings(month_start, end).filter(agent_id__in=ids).values_list("agent").annotate(n=Count("pk")))
    rows = []
    for a in agents:
        o, w = open_by.get(a.pk, {}), won.get(a.pk, {})
        n_assigned, n_won = assigned.get(a.pk, 0), w.get("n", 0)
        times = resp.get(a.pk)
        t = targets.get(a.pk)
        rev_m = month_rev.get(a.pk) or Decimal("0")
        rows.append({
            "agent": a, "open": o.get("n", 0), "pipeline": o.get("value") or Decimal("0"),
            "forecast": weighted.get(a.pk, Decimal("0")), "assigned": n_assigned, "won": n_won,
            "revenue": w.get("kes") or Decimal("0"), "quotes": quotes.get(a.pk, 0), "calls": calls_all.get(a.pk, 0),
            "connected": calls_conn.get(a.pk, 0), "overdue": overdue.get(a.pk, 0), "lost": lost.get(a.pk, 0),
            "breaches": breaches.get(a.pk, 0), "cold": cold.get(a.pk, 0),
            "conversion": round(100 * n_won / n_assigned) if n_assigned else 0,
            "response_min": round(sum(times) / len(times)) if times else None,
            "target": t, "month_revenue": rev_m, "month_bookings": month_bk.get(a.pk, 0),
            "revenue_pct": min(100, round(100 * rev_m / t.revenue_target)) if t and t.revenue_target else None,
            "bookings_pct": (min(100, round(100 * month_bk.get(a.pk, 0) / t.bookings_target))
                             if t and t.bookings_target else None),
        })
    return rows


def totals(rows):
    keys = ("open", "pipeline", "forecast", "assigned", "won", "revenue", "quotes", "calls", "connected", "overdue",
            "lost", "breaches", "cold")
    t = {k: sum(r[k] for r in rows) for k in keys}
    t["conversion"] = round(100 * t["won"] / t["assigned"]) if t["assigned"] else 0
    return t


def funnel(qs, start, end):
    """Enquiries created in range -> contacted -> quoted -> booked -> confirmed/completed."""
    base = qs.filter(created_at__date__gte=start, created_at__date__lte=end)
    live_booking = Booking.objects.filter(enquiry=OuterRef("pk")).exclude(status=BookingStatus.CANCELLED)
    base = base.annotate(has_booking=Exists(live_booking))
    steps = [
        ("Enquiries", base.count()),
        ("Contacted", base.filter(last_contact_at__isnull=False).count()),
        ("Quoted", base.exclude(quotation_status="not_quoted").count()),
        ("Booked", base.filter(has_booking=True).count()),
        ("Confirmed", base.filter(stage__in=[Stage.CONFIRMED, Stage.COMPLETED]).count()),
    ]
    top = steps[0][1] or 1
    return [{"label": l, "count": n, "pct": round(100 * n / top)} for l, n in steps]


def source_rows(qs, start, end):
    labels = __import__('apps.leads.models', fromlist=['x']).source_labels()
    live_booking = Booking.objects.filter(enquiry=OuterRef("pk")).exclude(status=BookingStatus.CANCELLED)
    base = qs.filter(created_at__date__gte=start, created_at__date__lte=end)
    rows = []
    for r in base.values("source").annotate(n=Count("pk")).order_by("-n"):
        src = base.filter(source=r["source"])
        won = src.filter(Exists(live_booking))
        rev = sum((b.total_kes for b in Booking.objects.filter(enquiry__in=won).exclude(
            status=BookingStatus.CANCELLED)), Decimal("0"))
        rows.append({"code": r["source"], "label": labels.get(r["source"], r["source"]), "enquiries": r["n"],
                     "won": won.count(), "revenue": rev,
                     "conversion": round(100 * won.count() / r["n"]) if r["n"] else 0})
    return rows


def package_rows(start, end, limit=10):
    """Best-selling packages in the period: bookings, travellers, revenue and how many enquiries asked for them."""
    bookings = (Booking.objects.exclude(status=BookingStatus.CANCELLED)
                .filter(booked_at__date__gte=start, booked_at__date__lte=end).select_related("package"))
    acc = {}
    for b in bookings:
        key = b.package_id or 0
        r = acc.setdefault(key, {"pk": key, "slug": b.package.slug if b.package_id else "", "name": b.package.title if b.package_id else "Custom itinerary",
                                 "bookings": 0, "travellers": 0, "revenue": Decimal("0")})
        r["bookings"] += 1
        r["travellers"] += (b.adults or 0) + (b.children or 0)
        r["revenue"] += b.total_kes
    asked = dict(Enquiry.objects.filter(created_at__date__gte=start, created_at__date__lte=end, package__isnull=False)
                 .values_list("package").annotate(n=Count("pk")))
    total_rev = sum((r["revenue"] for r in acc.values()), Decimal("0")) or Decimal("1")
    rows = sorted(acc.values(), key=lambda r: (-r["bookings"], -r["revenue"]))[:limit]
    for r in rows:
        r["enquiries"] = asked.get(r["pk"], 0)
        r["share"] = round(100 * r["revenue"] / total_rev)
    return rows


def lost_reasons(qs, start, end):
    return list(qs.filter(stage=Stage.LOST, stage_changed_at__date__gte=start, stage_changed_at__date__lte=end)
                .exclude(lost_reason="").values("lost_reason").annotate(n=Count("pk")).order_by("-n")[:8])


def monthly_revenue(months=6, agent_ids=None):
    today = timezone.localdate()
    first = (today.replace(day=1) - timedelta(days=31 * (months - 1))).replace(day=1)
    qs = Booking.objects.exclude(status=BookingStatus.CANCELLED).filter(booked_at__date__gte=first)
    t_qs = SalesTarget.objects.filter(year__gte=first.year)
    if agent_ids is not None:
        qs, t_qs = qs.filter(agent_id__in=agent_ids), t_qs.filter(agent_id__in=agent_ids)
    rev = {r["m"].strftime("%Y-%m"): r["kes"] for r in qs.annotate(m=TruncMonth("booked_at")).values("m")
           .annotate(kes=Sum(F("total_amount") * F("fx_rate")))}
    tgt = defaultdict(Decimal)
    for t in t_qs:
        tgt[f"{t.year}-{t.month:02d}"] += t.revenue_target
    labels, revenue, target = [], [], []
    cur = first
    for _ in range(months):
        key = cur.strftime("%Y-%m")
        labels.append(cur.strftime("%b"))
        revenue.append(float(rev.get(key) or 0))
        target.append(float(tgt.get(key) or 0))
        cur = (cur.replace(day=28) + timedelta(days=4)).replace(day=1)
    return {"labels": labels, "revenue": revenue, "target": target, "values": revenue}


def source_mix(qs):
    """Every lead the user can see, by where it came from."""
    from apps.leads.models import Source

    rows = (qs.values("source")
            .annotate(n=Count("pk")).order_by("-n"))
    labels = __import__('apps.leads.models', fromlist=['x']).source_labels()
    return {"labels": [labels.get(r["source"], r["source"]) for r in rows], "values": [r["n"] for r in rows],
            "codes": [r["source"] for r in rows]}


def weekly_leads(qs, weeks=10):
    """New leads per week against how many of them were won, for the last `weeks` weeks."""
    from django.db.models.functions import TruncWeek

    today = timezone.localdate()
    first = today - timedelta(days=today.weekday()) - timedelta(weeks=weeks - 1)
    new = {r["w"].date() if hasattr(r["w"], "date") else r["w"]: r["n"] for r in
           qs.filter(created_at__date__gte=first).annotate(w=TruncWeek("created_at")).values("w").annotate(n=Count("pk"))}
    won = {r["w"].date() if hasattr(r["w"], "date") else r["w"]: r["n"] for r in
           qs.filter(created_at__date__gte=first, stage__in=[Stage.CONFIRMED, Stage.COMPLETED])
           .annotate(w=TruncWeek("created_at")).values("w").annotate(n=Count("pk"))}
    labels, a, b = [], [], []
    for i in range(weeks):
        d = first + timedelta(weeks=i)
        labels.append(d.strftime("%d %b"))
        a.append(new.get(d, 0))
        b.append(won.get(d, 0))
    return {"labels": labels, "series": [{"label": "New leads", "data": a}, {"label": "Won", "data": b}], "values": a}


def stage_summary(qs):
    counts = {r["stage"]: r for r in qs.filter(stage__in=OPEN_STAGES + [Stage.CONFIRMED]).values("stage")
              .annotate(n=Count("pk"), value=Sum("deal_value"))}
    out = []
    for s in services.BOARD_STAGES:
        r = counts.get(s.value, {})
        out.append({"code": s.value, "label": s.label, "count": r.get("n", 0), "value": r.get("value") or 0})
    return out
