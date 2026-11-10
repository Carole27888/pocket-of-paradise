"""Sales automation: the system creates the follow-ups people would otherwise forget.

Every rule is idempotent: a task carries an `auto_key`, so running a rule twice never creates the same task twice.
Each rule can be switched off (or its timing changed) on Sales → Automation."""
from datetime import timedelta

from django.db.models import Exists, OuterRef
from django.utils import timezone

from apps.leads.models import OPEN_STAGES, Enquiry

from .models import Booking, BookingStatus, FollowUp, Quotation, QuotationStatus, SalesSettings, TripFeedback

RULES = [
    ("quote_chase", "Chase a quotation", "A quotation has been out for a few days with no answer: a task to ask the client for a decision.", "auto_quote_chase_days", "days"),
    ("reconnect", "Reconnect with a quiet lead", "An open lead has had no contact for a while: a task to get back in touch.", "auto_reconnect_days", "days"),
    ("deposit", "Chase an unpaid deposit", "A booking is still waiting for its deposit after a few days: a task to chase it.", "auto_deposit_chase_days", "days"),
    ("balance", "Chase the balance", "The trip is close and the balance is not paid in full: a task to collect it.", "auto_balance_chase_days", "days before travel"),
    ("pretrip", "Pre-trip check", "A week before travel: confirm documents, and the client's trip page is created for you.", "auto_pretrip_days", "days before travel"),
    ("feedback", "Ask for trip feedback", "The day after the client gets home: a task to ask them to rate the trip.", "auto_feedback_after_days", "days after return"),
]


def _make(enquiry, agent, kind, title, key, due=None):
    """Create the task once. Returns 1 when created, 0 when it already existed or the lead has no one to work it."""
    if not agent or FollowUp.objects.filter(auto_key=key).exists():
        return 0
    from .services import refresh_next_action

    FollowUp.objects.create(enquiry=enquiry, agent=agent, kind=kind, title=title[:160], due_at=due or timezone.now(), auto_key=key)
    refresh_next_action(enquiry)
    return 1


def quote_chase(cfg):
    if not cfg.auto_quote_chase_days:
        return {}
    out = {}
    cutoff = timezone.now() - timedelta(days=cfg.auto_quote_chase_days)
    for q in Quotation.objects.filter(status=QuotationStatus.SENT, sent_at__lt=cutoff).select_related("enquiry__client", "enquiry__assigned_agent"):
        e = q.enquiry
        if e.stage not in OPEN_STAGES or e.last_contact_at and e.last_contact_at > q.sent_at + timedelta(days=cfg.auto_quote_chase_days):
            continue
        n = _make(e, e.assigned_agent, "call", f"Chase {q.reference}: ask {e.client.full_name} for a decision", f"quote-chase:{q.pk}")
        if n:
            out[e.assigned_agent] = out.get(e.assigned_agent, 0) + n
    return out


def reconnect(cfg):
    if not cfg.auto_reconnect_days:
        return {}
    out = {}
    cutoff = timezone.now() - timedelta(days=cfg.auto_reconnect_days)
    open_task = FollowUp.objects.filter(enquiry=OuterRef("pk"), done_at__isnull=True)
    qs = (Enquiry.objects.filter(stage__in=OPEN_STAGES, assigned_agent__isnull=False, stage_changed_at__lt=cutoff)
          .exclude(last_contact_at__gte=cutoff).annotate(has_task=Exists(open_task)).filter(has_task=False)
          .select_related("client", "assigned_agent"))
    week = timezone.localdate().isocalendar()
    for e in qs[:300]:
        n = _make(e, e.assigned_agent, "whatsapp", f"Reconnect with {e.client.full_name}: no contact for {cfg.auto_reconnect_days}+ days", f"reconnect:{e.pk}:{week[0]}-{week[1]}")
        if n:
            out[e.assigned_agent] = out.get(e.assigned_agent, 0) + n
    return out


def _live_bookings():
    return Booking.objects.exclude(status__in=[BookingStatus.CANCELLED]).select_related("client", "agent", "enquiry")


def deposit(cfg):
    if not cfg.auto_deposit_chase_days:
        return {}
    out = {}
    cutoff = timezone.now() - timedelta(days=cfg.auto_deposit_chase_days)
    for b in _live_bookings().filter(status=BookingStatus.PENDING, booked_at__lt=cutoff):
        n = _make(b.enquiry, b.agent, "chase_payment", f"Chase the deposit for {b.reference} ({b.client.full_name})", f"deposit:{b.pk}")
        if n:
            out[b.agent] = out.get(b.agent, 0) + n
    return out


def balance(cfg):
    if not cfg.auto_balance_chase_days:
        return {}
    out = {}
    today = timezone.localdate()
    soon = today + timedelta(days=cfg.auto_balance_chase_days)
    for b in _live_bookings().filter(status__in=[BookingStatus.DEPOSIT], travel_start__isnull=False, travel_start__lte=soon, travel_start__gte=today):
        if b.balance <= 0:
            continue
        n = _make(b.enquiry, b.agent, "chase_payment", f"Collect the balance for {b.reference}: {b.currency} {b.balance:,.0f} before travel on {b.travel_start:%d %b}", f"balance:{b.pk}")
        if n:
            out[b.agent] = out.get(b.agent, 0) + n
    return out


def pretrip(cfg):
    if not cfg.auto_pretrip_days:
        return {}
    from .services import enable_portal

    out = {}
    today = timezone.localdate()
    soon = today + timedelta(days=cfg.auto_pretrip_days)
    for b in _live_bookings().filter(status__in=[BookingStatus.DEPOSIT, BookingStatus.CONFIRMED], travel_start__isnull=False, travel_start__lte=soon, travel_start__gte=today):
        enable_portal(b, None)    # the client's trip page is ready to send
        n = _make(b.enquiry, b.agent, "whatsapp", f"Pre-trip: confirm documents with {b.client.full_name} and send the trip page ({b.reference})", f"pretrip:{b.pk}")
        if n:
            out[b.agent] = out.get(b.agent, 0) + n
    return out


def feedback(cfg):
    if not cfg.auto_feedback_after_days:
        return {}
    out = {}
    today = timezone.localdate()
    done_by = today - timedelta(days=cfg.auto_feedback_after_days)
    has_fb = TripFeedback.objects.filter(booking=OuterRef("pk"))
    for b in (_live_bookings().filter(travel_end__isnull=False, travel_end__lte=done_by, travel_end__gte=today - timedelta(days=14))
              .annotate(has_fb=Exists(has_fb)).filter(has_fb=False)):
        n = _make(b.enquiry, b.agent, "whatsapp", f"Ask {b.client.full_name} to rate their trip: send the trip page ({b.reference})", f"feedback:{b.pk}")
        if n:
            out[b.agent] = out.get(b.agent, 0) + n
    return out


RUNNERS = {"quote_chase": quote_chase, "reconnect": reconnect, "deposit": deposit, "balance": balance, "pretrip": pretrip, "feedback": feedback}


def run_all(cfg=None) -> dict:
    """Run every enabled rule. Returns {rule key: tasks created}. Each agent gets one notification summarising their new tasks."""
    from apps.notifications.services import notify

    cfg = cfg or SalesSettings.get()
    if not cfg.automation_on:
        return {}
    summary, per_agent = {}, {}
    for key, fn in RUNNERS.items():
        made = fn(cfg)
        summary[key] = sum(made.values())
        for agent, n in made.items():
            per_agent[agent] = per_agent.get(agent, 0) + n
    today = timezone.localdate()
    for agent, n in per_agent.items():
        if agent and agent.user_id and n:
            notify(agent.user, f"{n} new follow-up{'s' if n != 1 else ''} for you", "Created automatically so nothing slips. Open Follow-ups to see them.", "/sales/followups/", "info",
                   dedupe_key=f"auto-followups:{agent.pk}:{today}")
    return summary
