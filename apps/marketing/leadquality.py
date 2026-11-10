"""Lead lifetime and lead quality for the social platforms: how far leads from each platform get through the pipeline."""
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from apps.accounts.roles import SOCIAL_SOURCES
from apps.leads.models import Enquiry, Stage

ORDER = [Stage.ENQUIRY, Stage.QUOTED, Stage.HOT, Stage.ITINERARY, Stage.DEPOSIT, Stage.CONFIRMED, Stage.COMPLETED]
IDX = {s.value: i for i, s in enumerate(ORDER)}
TERMINAL = {Stage.COMPLETED.value, Stage.LOST.value}
LABEL = dict(Stage.choices)


def stage_path(e):
    """[(stage, entered_at)] from the record's history, ending at the current stage. Old records with no history get one synthetic step."""
    path, last = [], None
    for r in e.history.order_by("history_date"):
        if r.stage != last:
            path.append((r.stage, r.history_date if path else e.created_at))
            last = r.stage
    if not path:
        path = [(Stage.ENQUIRY.value, e.created_at)]
    if path[-1][0] != e.stage:
        path.append((e.stage, e.stage_changed_at or e.updated_at))
    return path


def reached(e, path=None) -> int:
    """Furthest pipeline step this lead got to (0 = enquiry … 6 = trip completed); a lost lead keeps what it reached."""
    idx = max([IDX[s] for s, _ in (path or stage_path(e)) if s in IDX] or [0])
    if e.quotation_status and e.quotation_status != "not_quoted":
        idx = max(idx, 1)
    return idx


def lifetime(e):
    path = stage_path(e)
    end = (e.stage_changed_at or timezone.now()) if e.stage in TERMINAL else timezone.now()
    steps = []
    for i, (stage, at) in enumerate(path):
        nxt = path[i + 1][1] if i + 1 < len(path) else end
        steps.append({"stage": stage, "label": LABEL.get(stage, stage), "entered": at, "days": max(0, (nxt - at).total_seconds() / 86400) if stage not in TERMINAL else None})
    first_assign = e.assignments.order_by("assigned_at").first()
    return {"steps": steps, "days": max(0, (end - e.created_at).total_seconds() / 86400), "ended": e.stage in TERMINAL, "reached": reached(e, path),
            "assigned_after_h": round((first_assign.assigned_at - e.created_at).total_seconds() / 3600, 1) if first_assign else None,
            "first_contact_after_h": round((e.last_contact_at - e.created_at).total_seconds() / 3600, 1) if e.last_contact_at else None}


def quality_table(start, end, sources=SOCIAL_SOURCES):
    """One row per platform: leads and how many reached each step, conversion to a booking, time to book, value."""
    from apps.marketing.models import Channel

    names = {c.key: c.name for c in Channel.objects.all()}
    rows = []
    for src in sources:
        leads = list(Enquiry.objects.filter(source=src, created_at__date__range=(start, end)).prefetch_related("assignments"))
        n = len(leads)
        counts = [0] * len(ORDER)
        lost = booked_days = booked_n = 0
        won_value = open_value = Decimal("0")
        contacted = 0
        for e in leads:
            path = stage_path(e)
            r = reached(e, path)
            for i in range(r + 1):
                counts[i] += 1
            if e.stage == Stage.LOST.value:
                lost += 1
            if e.last_contact_at:
                contacted += 1
            if r >= IDX["deposit"]:
                at = next((t for s, t in path if IDX.get(s, -1) >= IDX["deposit"]), e.stage_changed_at)
                booked_days += max(0, (at - e.created_at).total_seconds() / 86400)
                booked_n += 1
                won_value += e.deal_value
            elif e.stage != Stage.LOST.value:
                open_value += e.deal_value
        booked = counts[IDX["deposit"]]
        rows.append({"source": src, "name": names.get(src, src.title()), "leads": n, "contacted": contacted, "steps": counts, "lost": lost, "booked": booked,
                     "conversion": round(100 * booked / n, 1) if n else 0, "quote_rate": round(100 * counts[1] / n) if n else 0,
                     "avg_days_to_book": round(booked_days / booked_n, 1) if booked_n else None, "won_value": won_value, "open_value": open_value})
    return sorted(rows, key=lambda r: (-r["conversion"], -r["leads"]))


def recent_lifetimes(start, end, sources=SOCIAL_SOURCES, channel="", stage="", limit=60):
    qs = Enquiry.objects.filter(source__in=[channel] if channel in sources else sources, created_at__date__range=(start, end)).select_related("client", "assigned_agent")
    if stage in Stage.values:
        qs = qs.filter(stage=stage)
    now = timezone.now()
    out = []
    for e in qs.order_by("-created_at")[:limit]:
        end_at = (e.stage_changed_at or now) if e.stage in TERMINAL else now
        out.append({"e": e, "age_days": round((end_at - e.created_at).total_seconds() / 86400, 1), "in_stage_days": round((now - (e.stage_changed_at or e.created_at)).total_seconds() / 86400, 1)
                    if e.stage not in TERMINAL else None, "reached": reached(e), "reached_label": LABEL.get(ORDER[reached(e)].value, "")})
    return out
