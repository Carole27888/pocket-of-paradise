"""Market research from the CRM's own enquiries, anonymised: no client names or contacts appear here.
Compares the last `days` with the `days` before, so a trend is a real change rather than one number."""
from collections import Counter, defaultdict
from datetime import timedelta
from statistics import median

from django.utils import timezone

from apps.leads.models import Enquiry, Stage


def _pct_change(now, before):
    if not before:
        return None if not now else 100
    return round(100 * (now - before) / before)


def _dest(e) -> str:
    return e.destination.name if e.destination_id else ((e.destination_text or "").strip().title() or "Not stated")


def demand(days: int = 90, now=None) -> dict:
    now = now or timezone.now()
    cur_start, prev_start = now - timedelta(days=days), now - timedelta(days=2 * days)
    qs = list(Enquiry.objects.filter(created_at__gte=prev_start).select_related("destination", "client", "package"))
    cur = [e for e in qs if e.created_at >= cur_start]
    prev = [e for e in qs if e.created_at < cur_start]

    def count(items, f):
        return Counter(f(e) for e in items)

    dests_c, dests_p = count(cur, _dest), count(prev, _dest)
    destinations = [{"name": n, "now": c, "before": dests_p.get(n, 0), "change": _pct_change(c, dests_p.get(n, 0)), "share": round(100 * c / len(cur)) if cur else 0}
                    for n, c in dests_c.most_common(8)]
    rising = sorted((d for d in destinations if d["change"] is not None and d["now"] >= 2), key=lambda d: -d["change"])[:3]
    cooling = sorted((d for d in destinations if d["change"] is not None and d["change"] < 0), key=lambda d: d["change"])[:3]

    markets_c = count(cur, lambda e: e.client.nationality or "Not stated")
    markets_p = count(prev, lambda e: e.client.nationality or "Not stated")
    markets = [{"name": n, "now": c, "before": markets_p.get(n, 0), "change": _pct_change(c, markets_p.get(n, 0))} for n, c in markets_c.most_common(6)]

    months = Counter()
    for e in cur:
        if e.travel_date_from:
            months[e.travel_date_from.strftime("%b %Y")] += 1
    travel_months = [{"name": n, "count": c} for n, c in sorted(months.items(), key=lambda kv: (-kv[1], kv[0]))[:6]]

    sizes = [e.adults + e.children for e in cur]
    budgets = [float(e.budget_max_pp or e.budget_min_pp) for e in cur if (e.budget_max_pp or e.budget_min_pp) and e.budget_currency == "KES"]
    lost = Counter((e.lost_reason or "No reason recorded") for e in cur if e.stage == Stage.LOST)
    sources = [{"name": n, "now": c} for n, c in count(cur, lambda e: e.get_source_display()).most_common(6)]
    won = sum(1 for e in cur if e.stage in (Stage.CONFIRMED, Stage.COMPLETED))
    return {"days": days, "total": len(cur), "before": len(prev), "change": _pct_change(len(cur), len(prev)), "destinations": destinations, "rising": rising, "cooling": cooling,
            "markets": markets, "travel_months": travel_months, "avg_group": round(sum(sizes) / len(sizes), 1) if sizes else None,
            "median_budget": round(median(budgets)) if budgets else None, "lost_reasons": lost.most_common(5), "sources": sources, "won": won,
            "families": sum(1 for e in cur if e.children), "families_pct": round(100 * sum(1 for e in cur if e.children) / len(cur)) if cur else 0,
            "headlines": headlines(len(cur), len(prev), rising, cooling, travel_months, lost)}


def headlines(n, before, rising, cooling, months, lost) -> list:
    out = []
    ch = _pct_change(n, before)
    if n:
        out.append(f"{n} enquiries in this period, {'up' if (ch or 0) >= 0 else 'down'} {abs(ch or 0)}% on the one before." if before else f"{n} enquiries in this period; there is no earlier period to compare with yet.")
    if rising:
        r = rising[0]
        out.append(f"{r['name']} is rising: {r['now']} enquiries against {r['before']} before. Worth featuring in posts and offers.")
    if cooling:
        c = cooling[0]
        out.append(f"{c['name']} is cooling ({c['change']}%). Check pricing, content and follow-up there.")
    if months:
        out.append(f"Most requested travel month: {months[0]['name']} ({months[0]['count']} enquiries). Make sure departures and availability cover it.")
    if lost:
        out.append(f"Top reason for lost deals: “{lost.most_common(1)[0][0]}”.")
    return out
