"""Write and deliver the assistant's messages. Plain templates always work; AI only improves the wording when a key is saved."""
import logging
from datetime import datetime, timedelta

from django.utils import timezone

from apps.accounts import roles as R
from apps.notifications.services import notify
from apps.people.models import Employee

from . import facts
from .models import CoachMessage, CoachSettings

log = logging.getLogger(__name__)


# ------------------------------------------------------------------ recipients
def agents():
    return Employee.objects.filter(is_sales_agent=True, is_active=True, user__isnull=False, user__is_active=True).select_related("user")


def managers():
    return Employee.objects.filter(is_active=True, user__isnull=False, user__is_active=True, user__groups__name__in=[R.SALES_MANAGER, R.COO, R.DIRECTOR]).distinct().select_related("user")


# ------------------------------------------------------------------ delivery
def _word(cfg, kind, plain: str, facts_for_ai: dict):
    """-> (text, written_by_ai). The AI may only rephrase the facts it is given."""
    if not cfg.use_ai:
        return plain, False
    try:
        from apps.ict import ai

        if not ai.configured():
            return plain, False
        system = ("You write short, warm, practical messages for a safari company's sales team. Use ONLY the facts provided: never invent names, numbers, dates or promises. "
                  "Keep it under 120 words, plain text, no markdown headings, end with one clear next step. Currency amounts are as given.")
        resp = ai.complete(system, [{"role": "user", "content": f"Message type: {kind}\nFacts: {facts_for_ai}\nDraft to improve:\n{plain}"}], max_tokens=350, feature="coach")
        text = ai.text_of(resp)
        return (text, True) if text else (plain, False)
    except Exception:  # AI down, no quota, anything: the plain version is always fine
        return plain, False


def send(emp, kind, key, title, plain, *, enquiry=None, facts_data=None, cfg=None, url="/coach/"):
    """Create the message once per employee, key and day, then alert in-app and (if switched on) by e-mail and WhatsApp."""
    cfg = cfg or CoachSettings.load()
    today = timezone.localdate()
    if CoachMessage.objects.filter(employee=emp, key=key, day=today).exists():
        return None
    body, ai_used = _word(cfg, kind, plain, facts_data or {})
    msg = CoachMessage.objects.create(employee=emp, kind=kind, key=key, day=today, title=title[:160], body=body, enquiry=enquiry, facts=facts_data or {}, written_by_ai=ai_used)
    if emp.user_id:
        notify(emp.user, title, body[:140], enquiry.get_absolute_url() if enquiry else url, "warning" if kind in (CoachMessage.NUDGE, CoachMessage.ALERT) else "info", push=True)
    try:
        from apps.integrations.messaging import send_message

        if cfg.send_email and emp.user and emp.user.email:
            send_message("email", emp.user.email, body, subject=title, purpose=f"coach_{kind}", related=f"coach:{msg.pk}")
        if cfg.send_whatsapp and emp.phone:
            send_message("whatsapp", emp.phone, f"{title}\n\n{body}", purpose=f"coach_{kind}", related=f"coach:{msg.pk}")
    except Exception:
        log.exception("coach delivery failed")
    return msg


def _first(name):
    return (name or "").split(" ")[0] or "there"


def _bullets(items, fmt, limit=5):
    lines = [f"• {fmt(i)}" for i in items[:limit]]
    if len(items) > limit:
        lines.append(f"…and {len(items) - limit} more")
    return "\n".join(lines)


# ------------------------------------------------------------------ morning briefing
def briefing_facts(emp, now=None):
    now = now or timezone.now()
    today = timezone.localdate()
    cfg = CoachSettings.load()
    return {"uncontacted": facts.uncontacted(emp, 0, now), "followups": facts.followups(emp, now), "stale": facts.stale_leads(emp, cfg.stale_lead_days, now),
            "quotes": facts.stale_quotes(emp, cfg.stale_quote_days, now), "bookings": facts.bookings_to_chase(emp, now), "departures": facts.departures_soon(emp, today),
            "todos": facts.todos(emp), "month": facts.month(emp, today)}


def plain_briefing(emp, f):
    parts = [f"Good morning {_first(emp.full_name)}. Here is your day."]
    if f["uncontacted"]:
        parts.append(f"New leads to call first ({len(f['uncontacted'])}):\n" + _bullets(f["uncontacted"], lambda r: f"{r['client']} · {r['interest']} (waiting {r['minutes'] // 60}h {r['minutes'] % 60}m)"))
    if f["followups"]:
        parts.append(f"Follow-ups due ({len(f['followups'])}):\n" + _bullets(f["followups"], lambda r: f"{r['title']} · {r['client']} · {r['due']}{' (late)' if r['late'] else ''}"))
    if f["quotes"]:
        parts.append("Quotations with no reply:\n" + _bullets(f["quotes"], lambda r: f"{r['client']} · {r['quote']} {r['total']} (sent {r['days']} days ago)"))
    if f["bookings"]:
        parts.append("Payments to chase:\n" + _bullets(f["bookings"], lambda r: f"{r['client']} · {r['ref']}: {r['what']}"))
    if f["departures"]:
        parts.append("Travelling this week:\n" + _bullets(f["departures"], lambda r: f"{r['client']} · {r['ref']} on {r['date']}"))
    if f["todos"]:
        parts.append("Your to-dos:\n" + _bullets(f["todos"], str))
    m = f["month"]
    if m.get("pct") is not None:
        parts.append(f"This month: {m['pct']}% of your KES {float(m['target']):,.0f} target" + (f", rank {m['rank']} of {m['of']}." if m.get("rank") else "."))
    if len(parts) == 1:
        parts.append("Nothing urgent is waiting. A good moment to follow up on warm leads.")
    return "\n\n".join(parts)


def ensure_briefing(user):
    """Called when a sales agent opens the main menu: the first visit of the day creates the briefing."""
    cfg = CoachSettings.load()
    emp = getattr(user, "employee", None)
    if not cfg.briefing_on or emp is None or not emp.is_sales_agent:
        return None
    f = briefing_facts(emp)
    return send(emp, CoachMessage.BRIEFING, "briefing", f"Your day, {timezone.localdate():%A %d %b}", plain_briefing(emp, f), facts_data={k: v for k, v in f.items() if k != "todos"}, cfg=cfg)


def send_morning_briefings(now=None):
    cfg = CoachSettings.load()
    now = timezone.localtime(now or timezone.now())
    if not cfg.briefing_on or now.time() < cfg.briefing_at or _off_day(cfg, now):
        return 0
    n = 0
    for emp in agents():
        n += bool(ensure_briefing(emp.user))
    return n


# ------------------------------------------------------------------ nudges
def _off_day(cfg, now):
    return cfg.no_sundays and now.weekday() == 6


def _in_hours(cfg, now):
    return cfg.work_starts <= now.time() <= cfg.work_ends and not _off_day(cfg, now)


def run_nudges(now=None) -> int:
    cfg = CoachSettings.load()
    now = timezone.localtime(now or timezone.now())
    if not cfg.nudges_on or not _in_hours(cfg, now):
        return 0
    n = 0
    today = timezone.localdate()
    for emp in agents():
        sent_today = CoachMessage.objects.filter(employee=emp, kind=CoachMessage.NUDGE, day=today).count()
        todo = []
        for r in facts.uncontacted(emp, cfg.uncontacted_minutes, now):
            todo.append((f"unc:{r['id']}", f"{r['client']} hasn't been contacted", f"{r['client']} ({r['interest']}) has been waiting {r['minutes']} minutes with no call. A quick call or WhatsApp now keeps the lead warm.", r["id"]))
        for r in facts.stale_leads(emp, cfg.stale_lead_days, now):
            todo.append((f"stale:{r['id']}", f"{r['client']} has gone quiet", f"No contact with {r['client']} for {r['days']} days (now {r['stage']}). Send a short check-in.", r["id"]))
        for r in facts.stale_quotes(emp, cfg.stale_quote_days, now):
            todo.append((f"quote:{r['id']}", f"No reply to {r['quote']}", f"{r['client']} hasn't answered quotation {r['quote']} ({r['total']}) after {r['days']} days. Ask if they have questions.", r["id"]))
        for f in facts.followups(emp, now):
            if f["late"]:
                todo.append((f"fu:{f['title'][:40]}:{f['client'][:20]}", f"Overdue: {f['title']}", f"“{f['title']}” for {f['client']} was due {f['due']}.", None))
        for key, title, text, eid in todo:
            if sent_today >= cfg.max_nudges_per_day:
                break
            from apps.leads.models import Enquiry

            if send(emp, CoachMessage.NUDGE, key, title, text, enquiry=Enquiry.objects.filter(pk=eid).first() if eid else None, cfg=cfg):
                sent_today += 1
                n += 1
    return n + run_manager_alerts(cfg, now)


def run_manager_alerts(cfg, now) -> int:
    from apps.leads.models import Enquiry, Stage

    cutoff = now - timedelta(hours=cfg.manager_alert_hours)
    late = [e for e in Enquiry.objects.filter(stage=Stage.ENQUIRY, last_contact_at__isnull=True, created_at__lte=cutoff).select_related("client", "assigned_agent")[:30]]
    n = 0
    for e in late:
        who = e.assigned_agent.full_name if e.assigned_agent_id else "nobody (still waiting at the Front Desk)"
        for mgr in managers():
            n += bool(send(mgr, CoachMessage.ALERT, f"late:{e.pk}", f"Lead untouched for {cfg.manager_alert_hours}+ hours", f"{e.client.full_name} ({e.interest_label}, {e.reference}) has had no contact. With: {who}.", enquiry=e, cfg=cfg))
    return n


# ------------------------------------------------------------------ end of day / weekly / digests
def _window(day, days=1):
    start = timezone.make_aware(datetime.combine(day, datetime.min.time()))
    return start, start + timedelta(days=days)


def plain_report(emp, a, label):
    return (f"{label} for {_first(emp.full_name)}:\n• Calls: {a['calls']} ({a['connected']} connected)\n• WhatsApp and e-mails: {a['whatsapp'] + a['emails']}\n• Quotations sent: {a['quotes']}\n"
            f"• New leads given to you: {a['new_leads']}\n• Stage moves: {a['stage_moves']}\n• Bookings made: {a['bookings']}")


def send_end_of_day(now=None) -> int:
    cfg = CoachSettings.load()
    now = timezone.localtime(now or timezone.now())
    if not cfg.end_of_day_on or now.time() < cfg.end_of_day_at or _off_day(cfg, now):
        return 0
    today = timezone.localdate()
    s, e = _window(today)
    n = 0
    for emp in agents():
        a = facts.activity(emp, s, e)
        m = facts.month(emp, today)
        text = plain_report(emp, a, "Today") + (f"\n\nMonth so far: {m['pct']}% of target" + (f", rank {m['rank']} of {m['of']}" if m.get("rank") else "") + "." if m.get("pct") is not None else "")
        left = len(facts.followups(emp)) + len(facts.uncontacted(emp, 0))
        if left:
            text += f"\n\nStill open for tomorrow: {left} item{'s' if left != 1 else ''}."
        n += bool(send(emp, CoachMessage.EOD, "eod", f"Your day in numbers · {today:%d %b}", text, facts_data={"activity": a, "month": m}, cfg=cfg))
    return n


def send_weekly(now=None) -> int:
    cfg = CoachSettings.load()
    now = timezone.localtime(now or timezone.now())
    if not cfg.weekly_on or now.weekday() != 0 or now.time() < cfg.weekly_at:
        return 0
    today = timezone.localdate()
    s, e = _window(today - timedelta(days=7), 7)
    n = 0
    for emp in agents():
        a = facts.activity(emp, s, e)
        m = facts.month(emp, today)
        text = plain_report(emp, a, "Last week") + (f"\n\nThis month: {m['pct']}% of target." if m.get("pct") is not None else "")
        n += bool(send(emp, CoachMessage.WEEKLY, "weekly", f"Your week · {s:%d %b} to {(e - timedelta(days=1)):%d %b}", text, facts_data={"activity": a, "month": m}, cfg=cfg))
    return n


def team_rows(today):
    from apps.sales import analytics

    return sorted(analytics.agent_rows(today.replace(day=1), today), key=lambda r: -r["month_revenue"])


def send_digest(now=None) -> int:
    cfg = CoachSettings.load()
    now = timezone.localtime(now or timezone.now())
    if not cfg.digest_on or now.time() < cfg.digest_at or _off_day(cfg, now):
        return 0
    today = timezone.localdate()
    rows = team_rows(today)
    s, e = _window(today)
    lines = [f"{r['agent'].full_name}: {r['calls']} calls month-to-date, {r['open']} open, {r['overdue']} follow-ups overdue" + (f", {r['revenue_pct']}% of target" if r["revenue_pct"] is not None else "") for r in rows[:8]]
    from apps.leads.models import Enquiry

    waiting = Enquiry.objects.filter(assigned_agent__isnull=True, stage__in=["enquiry", "quoted", "hot"]).count()
    text = f"Team digest for {today:%A %d %b}.\n\n" + "\n".join(f"• {l}" for l in lines) + f"\n\nLeads still waiting for an agent: {waiting}."
    return sum(bool(send(m, CoachMessage.DIGEST, "digest", f"Team digest · {today:%d %b}", text, cfg=cfg)) for m in managers())


def send_exec_briefing(now=None) -> int:
    cfg = CoachSettings.load()
    now = timezone.localtime(now or timezone.now())
    if not cfg.exec_briefing_on or now.time() < cfg.exec_briefing_at or _off_day(cfg, now):
        return 0
    from apps.insights import executive

    today = timezone.localdate()
    c = executive.centre()
    text = (f"Good morning. {today:%A %d %b}.\n\n• Revenue this month: KES {c['revenue']:,.0f}" + (f" ({c['revenue_pct']}% of target)" if c.get("revenue_pct") is not None else "") +
            f"\n• Open pipeline: {c['open_leads']} leads, KES {c['pipeline']:,.0f}\n• Waiting for an agent: {c['waiting']}\n• Overdue follow-ups: {c['overdue_followups']}\n• Departures this week: {c['departures_week']}\n• Overdue invoices: {c['overdue_invoices']}")
    execs = Employee.objects.filter(is_active=True, user__isnull=False, user__groups__name__in=[R.COO, R.DIRECTOR]).distinct()
    return sum(bool(send(m, CoachMessage.EXEC, "exec", f"Morning briefing · {today:%d %b}", text, facts_data=c, cfg=cfg, url="/insights/")) for m in execs)
