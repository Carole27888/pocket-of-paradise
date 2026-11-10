"""The business assistant for the director and COO on WhatsApp.

Someone texts the company WhatsApp number. If the number is on the profile of an active user who holds one of the roles set in
ICT → AI (Director and COO by default), the question goes here instead of the customer bot. The AI answers with live figures through
read-only tools; it never sees anything the tools don't return and cannot change data. Anybody else never reaches these tools.
"""
import json
import logging
import re
from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, F, Sum
from django.utils import timezone

from apps.core.phone import normalise_phone

from . import ai, assistant

log = logging.getLogger(__name__)

SYSTEM = """You are the business assistant of {company}, a Kenyan safari company, answering {name} ({role}) on WhatsApp.
Answer questions about the business using ONLY the tools: leads, sales, bookings, agents, departures, revenue and payments.
Rules: never guess a figure, call a tool. Keep answers short for a phone screen: plain text, no tables, at most about 12 lines,
lists with "-". Money in KES unless the tool gives another currency; round sensibly (e.g. KES 1.2M). Say which period a number covers.
If a tool returns nothing, say so plainly. You cannot change any data. Today is {today}."""

PERIOD = {"type": "string", "enum": ["today", "week", "month", "quarter", "year"], "description": "today, this week, this month (default), this quarter, this year"}
TOOLS = [
    {"name": "business_overview", "description": "Headline numbers for a period: new leads, open pipeline, bookings won and their value, payments received, conversion, leads waiting to be assigned.",
     "input_schema": {"type": "object", "properties": {"period": PERIOD}}},
    {"name": "agent_performance", "description": "Every sales agent for a period: leads assigned, bookings won, revenue, conversion, open leads, overdue follow-ups, average response time. Sorted by revenue.",
     "input_schema": {"type": "object", "properties": {"period": PERIOD}}},
    {"name": "upcoming_departures", "description": "Clients travelling in the next N days (default 14): date, client, trip, agent, paid and balance.",
     "input_schema": {"type": "object", "properties": {"days": {"type": "integer"}}}},
    {"name": "lead_sources", "description": "Where leads came from in a period, with how many became bookings and the revenue.",
     "input_schema": {"type": "object", "properties": {"period": PERIOD}}},
    {"name": "pipeline_by_stage", "description": "How many open leads are at each stage right now, and their value.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "outstanding_balances", "description": "Won bookings with money still to collect, biggest first.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "top_packages", "description": "Best-selling packages in a period.", "input_schema": {"type": "object", "properties": {"period": PERIOD}}},
    *[t for t in assistant.TOOLS if t["name"] in ("search_enquiries", "search_bookings", "search_clients", "search_packages")],
]


def _kes(v) -> str:
    v = Decimal(v or 0)
    if abs(v) >= 1_000_000:
        return f"KES {v / 1_000_000:.1f}M"
    if abs(v) >= 10_000:
        return f"KES {v / 1000:.0f}K"
    return f"KES {v:,.0f}"


def run_tool(user, name: str, args: dict):
    from apps.leads.models import OPEN_STAGES, Enquiry
    from apps.sales import analytics
    from apps.sales.models import Booking, BookingPayment, BookingStatus

    start, end, key = analytics.period(args.get("period") or "month")
    label = dict(analytics.RANGES).get(key, key)
    if name == "business_overview":
        new = Enquiry.objects.filter(created_at__date__gte=start, created_at__date__lte=end)
        won = analytics._bookings(start, end)
        paid = BookingPayment.objects.filter(received_at__date__gte=start, received_at__date__lte=end).aggregate(kes=Sum(F("amount") * F("booking__fx_rate")))["kes"]
        open_qs = Enquiry.objects.filter(stage__in=OPEN_STAGES)
        return {"period": f"{label} ({start:%d %b} to {end:%d %b %Y})", "new_leads": new.count(), "bookings_won": won.count(),
                "bookings_value": _kes(won.aggregate(v=Sum(F("total_amount") * F("fx_rate")))["v"]), "payments_received": _kes(paid),
                "conversion_of_new_leads": f"{round(100 * new.filter(booking__isnull=False).count() / new.count()) if new.count() else 0}%",
                "open_leads_now": open_qs.count(), "open_pipeline_value": _kes(open_qs.aggregate(v=Sum("deal_value"))["v"]),
                "waiting_unassigned": open_qs.filter(assigned_agent__isnull=True).count()}
    if name == "agent_performance":
        rows = sorted(analytics.agent_rows(start, end), key=lambda r: -r["revenue"])
        return {"period": label, "agents": [{"agent": r["agent"].full_name, "leads_assigned": r["assigned"], "won": r["won"], "revenue": _kes(r["revenue"]),
                                             "conversion": f"{r['conversion']}%", "open": r["open"], "overdue_followups": r["overdue"],
                                             "avg_response_min": r["response_min"]} for r in rows]}
    if name == "upcoming_departures":
        days = max(1, min(int(args.get("days") or 14), 90))
        today = timezone.localdate()
        qs = (Booking.objects.filter(status__in=[BookingStatus.DEPOSIT, BookingStatus.CONFIRMED, BookingStatus.PENDING], travel_start__gte=today,
                                     travel_start__lte=today + timedelta(days=days)).select_related("client", "agent").order_by("travel_start")[:25])
        return {"next_days": days, "trips": [{"date": f"{b.travel_start:%a %d %b}", "client": b.client.full_name, "trip": b.trip_name or b.title, "pax": b.party_label,
                                              "agent": b.agent.full_name if b.agent_id else None, "status": b.get_status_display(),
                                              "balance": f"{b.currency} {b.balance:,.0f}"} for b in qs] or "No trips in that window."}
    if name == "lead_sources":
        return {"period": label, "sources": [{"source": r["label"], "leads": r["enquiries"], "won": r["won"], "revenue": _kes(r["revenue"]), "conversion": f"{r['conversion']}%"}
                                             for r in analytics.source_rows(Enquiry.objects.all(), start, end)]}
    if name == "pipeline_by_stage":
        rows = Enquiry.objects.filter(stage__in=OPEN_STAGES).values("stage").annotate(n=Count("pk"), v=Sum("deal_value")).order_by("-n")
        labels = dict(Enquiry._meta.get_field("stage").choices)
        return [{"stage": labels.get(r["stage"], r["stage"]), "leads": r["n"], "value": _kes(r["v"])} for r in rows]
    if name == "outstanding_balances":
        qs = Booking.objects.filter(status__in=[BookingStatus.DEPOSIT, BookingStatus.CONFIRMED, BookingStatus.PENDING]).select_related("client", "agent").prefetch_related("payments")
        rows = sorted((b for b in qs if b.balance > 0), key=lambda b: -b.balance * b.fx_rate)[:12]
        return {"total_outstanding": _kes(sum(b.balance * b.fx_rate for b in rows)), "bookings": [
            {"ref": b.reference, "client": b.client.full_name, "travel": f"{b.travel_start:%d %b}" if b.travel_start else None, "balance": f"{b.currency} {b.balance:,.0f}",
             "agent": b.agent.full_name if b.agent_id else None} for b in rows]}
    if name == "top_packages":
        return {"period": label, "packages": [{"package": r.get("name"), "bookings": r.get("bookings"), "revenue": _kes(r.get("revenue"))} for r in analytics.package_rows(start, end, 8)]}
    return assistant.run_tool(user, name, args)


def exec_user_for(phone: str):
    """The active director/COO (or other role set in ICT → AI) whose profile has this WhatsApp number; else None."""
    from apps.accounts.models import User

    from .models import MessagingSettings

    cfg = MessagingSettings.get()
    if not cfg.exec_assistant_enabled:
        return None
    phone = normalise_phone(phone)
    if not phone:
        return None
    roles = cfg.exec_role_names
    for u in User.objects.filter(is_active=True).select_related("employee").prefetch_related("groups"):
        if normalise_phone(u.whatsapp_target) == phone and (u.is_superuser and "Admin" in roles or u.role_names.intersection(roles)):
            return u
    return None


def _history(phone: str) -> list:
    from .models import ExecChat

    out = []
    for c in reversed(list(ExecChat.objects.filter(phone=phone, created_at__gte=timezone.now() - timedelta(hours=6), ok=True)[:4])):
        out += [{"role": "user", "content": c.question}, {"role": "assistant", "content": c.answer}]
    return out


def answer(user, phone: str, question: str) -> str:
    """Ask the AI with tools, log the exchange and return the reply text (a plain apology when the AI is unavailable)."""
    from apps.core.models import CompanySetting

    from .models import ExecChat

    phone = normalise_phone(phone)
    q = question.strip()[:800]
    if re.fullmatch(r"(?i)\s*(hi|hello|hey|help|menu|start)\W*", q):
        reply = (f"Hello {user.full_name.split()[0]}. Ask me about the business, for example:\n- How many leads came in this week?\n- Which agent is performing best this month?\n"
                 "- Upcoming departures in the next 7 days\n- Revenue this month\n- Who still owes us money?")
        ExecChat.objects.create(user=user, phone=phone, question=q, answer=reply, provider="")
        return reply
    system = SYSTEM.format(company=CompanySetting.load().name or "Pocket of Paradise", name=user.full_name, role=user.primary_role, today=timezone.localdate().strftime("%A %d %B %Y"))
    msgs = _history(phone) + [{"role": "user", "content": q}]
    provider = ""
    try:
        for _ in range(5):
            resp = ai.complete(system, msgs, tools=TOOLS, max_tokens=700, feature="exec")
            provider = resp.get("provider", ai.active_provider())
            if resp.get("stop_reason") != "tool_use":
                reply = ai.text_of(resp) or "I couldn't find an answer to that."
                break
            msgs.append({"role": "assistant", "content": resp["content"]})
            results = []
            for b in resp["content"]:
                if b.get("type") == "tool_use":
                    try:
                        out = run_tool(user, b["name"], b.get("input") or {})
                    except Exception as e:  # a tool bug must not leave the director without an answer
                        log.exception("exec tool %s failed", b["name"])
                        out = {"error": f"That figure isn't available right now ({e.__class__.__name__})."}
                    results.append({"type": "tool_result", "tool_use_id": b["id"], "content": json.dumps(out, default=str)})
            msgs.append({"role": "user", "content": results})
        else:
            reply = "That needed too many steps. Try asking one thing at a time."
        ExecChat.objects.create(user=user, phone=phone, question=q, answer=reply[:4000], provider=provider)
        return reply[:4000]
    except ai.AIUnavailable as e:
        reply = "The business assistant can't reach the AI right now. Check the AI key under ICT → AI, or try again in a few minutes."
        ExecChat.objects.create(user=user, phone=phone, question=q, answer=f"{reply} ({e})", provider=provider, ok=False)
        return reply


def handle(phone: str, text: str, external_id: str = "") -> bool:
    """Called for every inbound WhatsApp text. True when it came from an executive and was answered here (so the customer bot skips it)."""
    from apps.integrations.messaging import send_message

    user = exec_user_for(phone)
    if user is None:
        return False
    reply = answer(user, phone, text)
    send_message("whatsapp", phone, reply, purpose="exec_assistant", related=f"user:{user.pk}", user=user)
    return True
