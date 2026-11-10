from datetime import timedelta

from django.utils import timezone

from apps.accounts import roles
from apps.core.jobs import daily, every, task
from apps.notifications.services import notify, notify_roles

from . import services


@every(minutes=10)
@task("sales.detect_double_allocations")
def detect_double_allocations():
    n = services.detect_double_allocations()
    if n:
        notify_roles([roles.SALES_MANAGER], f"{n} new double allocation{'s' if n != 1 else ''}",
                     "The same guest is being worked by two consultants.", "/sales/double-allocation/", "warning",
                     dedupe_key=f"double:{timezone.localdate()}:{n}")
    return f"{n} new"


@daily(hour=7, minute=30)
@task("sales.automation")
def sales_automation():
    """Create the follow-ups nobody should have to remember: quote chases, quiet leads, deposits, balances, pre-trip checks, feedback."""
    from . import automation

    made = automation.run_all()
    return ", ".join(f"{k}: {v}" for k, v in made.items() if v) or "nothing to create"


@daily(hour=8, minute=15)
@task("sales.quote_expiry_and_followups")
def quote_expiry_and_followups():
    """Quotes expiring tomorrow, stale quotes expire, deposits overdue: nudge the consultant."""
    from .models import Booking, BookingStatus, FollowUp, Quotation, QuotationStatus

    today = timezone.localdate()
    n = 0
    for q in Quotation.objects.filter(status=QuotationStatus.SENT, valid_until=today + timedelta(days=1)).select_related("agent__user", "enquiry__client"):
        if q.agent and q.agent.user:
            n += bool(notify(q.agent.user, f"{q.reference} expires tomorrow", q.enquiry.client.full_name, q.get_absolute_url(),
                             "warning", dedupe_key=f"qexp:{q.pk}"))
    expired = Quotation.objects.filter(status=QuotationStatus.SENT, valid_until__lt=today).update(status=QuotationStatus.EXPIRED)
    for b in Booking.objects.filter(status=BookingStatus.PENDING, booked_at__lt=timezone.now() - timedelta(days=3)).select_related("agent__user", "client"):
        if b.agent and b.agent.user:
            n += bool(notify(b.agent.user, f"Deposit overdue: {b.reference}", b.client.full_name, b.get_absolute_url(), "warning",
                             dedupe_key=f"dep:{b.pk}:{today:%Y-%m-%d}"))
    overdue = FollowUp.objects.filter(done_at__isnull=True, due_at__lt=timezone.now() - timedelta(hours=4)).select_related("agent__user")
    for f in overdue[:200]:
        if f.agent and f.agent.user:
            n += bool(notify(f.agent.user, "Follow-up overdue", f.title, f.enquiry.get_absolute_url(), "warning",
                             dedupe_key=f"fu:{f.pk}:{today:%Y-%m-%d}", push=False))
    return f"{n} nudges, {expired} quotes expired"


@daily(hour=8, minute=30)
@task("sales.itinerary_nudges")
def itinerary_nudges():
    """Itineraries that went out and went quiet: not opened, opened many times with no reply, or sent without a quotation behind them."""
    from apps.leads.models import Stage

    from .models import LeadItinerary

    now, today, n = timezone.now(), timezone.localdate(), 0
    live = LeadItinerary.objects.filter(status="sent", enquiry__stage__in=[Stage.ITINERARY, Stage.QUOTED, Stage.HOT]).select_related("enquiry__client", "enquiry__assigned_agent__user")
    for it in live:
        agent = it.enquiry.assigned_agent
        if not (agent and agent.user_id):
            continue
        name = it.enquiry.client.full_name
        if it.share_token and it.share_first_viewed_at is None and it.shared_at and it.shared_at < now - timedelta(days=2):
            n += bool(notify(agent.user, f"{name} hasn't opened the itinerary", "Sent 2+ days ago. Nudge them on WhatsApp.", it.get_absolute_url(), "warning", dedupe_key=f"itin-unopened:{it.pk}"))
        elif it.share_views >= 3 and not it.responses.exists():
            n += bool(notify(agent.user, f"{name} keeps coming back to the itinerary", f"Opened {it.share_views} times with no reply. Call now.", it.get_absolute_url(), "success",
                             dedupe_key=f"itin-hot:{it.pk}:{it.share_views // 3}"))
        if it.quotation_id is None and it.sent_at and it.sent_at < now - timedelta(days=1):
            n += bool(notify(agent.user, f"Quote {name}", "The itinerary is out but there is no quotation yet.", it.get_absolute_url(), "info", dedupe_key=f"itin-noquote:{it.pk}", push=False))
    return f"{n} nudges"


@every(minutes=30)
@task("sales.travel_reminders")
def travel_reminders():
    """Days before each won trip (ICT → WhatsApp sets which days and the hour), remind the agent and the client on WhatsApp."""
    from . import reminders

    return f"{reminders.run()} reminders sent"
