"""Travel reminders: N days before a won booking departs (5 by default, more can be added in ICT → WhatsApp), the agent gets a WhatsApp
with a link to the travel-day preview, and the client gets a WhatsApp with a link to their own trip page (no login).

Each reminder goes once (TravelReminder is unique per booking, day and audience). When a booking is made late, only the closest
reminder still ahead is sent, never a "5 days to go" message two days before."""
from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

from .models import Booking, BookingStatus, TravelReminder

WON = [BookingStatus.DEPOSIT, BookingStatus.CONFIRMED]


def _settings():
    from apps.ict.models import MessagingSettings

    return MessagingSettings.get()


def due(today=None) -> list[tuple[Booking, int]]:
    """(booking, reminder day) pairs that should go out now."""
    today = today or timezone.localdate()
    days = _settings().reminder_days
    out = []
    qs = (Booking.objects.filter(status__in=WON, travel_start__gte=today, travel_start__lte=today + timedelta(days=max(days)))
          .select_related("client", "agent__user", "departure"))
    for b in qs:
        left = (b.travel_start - today).days
        ahead = [d for d in days if d >= left]
        if ahead:
            out.append((b, min(ahead)))
    return out


def client_needs(b: Booking) -> str:
    """A short "please have ready" list for the client's message."""
    items = ["passport" if b.client.is_international else "ID"]
    if b.client.is_international:
        items.append("Kenya eTA")
    if b.balance > 0:
        items.append(f"the balance of {b.currency} {b.balance:,.0f}")
    dep = b.departure
    if dep and dep.pickup_time:
        items.append(f"pick-up at {dep.pickup_time:%H:%M} from {dep.meeting_point}")
    return ", ".join(items)


def _when(b: Booking) -> str:
    return f"{b.travel_start:%a %d %b %Y}"


def send_for(b: Booking, d: int) -> list[TravelReminder]:
    from apps.notifications.services import absolute, notify

    from . import services

    cfg, sent = _settings(), []
    trip = b.trip_name or b.title
    if cfg.remind_agents and not TravelReminder.objects.filter(booking=b, days_before=d, audience=TravelReminder.AGENT).exists():
        user = b.agent.user if b.agent_id and b.agent.user_id else None
        link = absolute(reverse("sales:booking_travel_day", args=[b.pk]))
        ok = bool(user and notify(user, f"{b.client.full_name} travels {_when(b)}", f"{trip} · {b.reference}. Check the travel-day preview.",
                                  reverse("sales:booking_travel_day", args=[b.pk]), "info", dedupe_key=f"travel:{b.pk}:{d}", purpose="travel_agent",
                                  fields={"client": b.client.full_name, "date": _when(b), "trip": trip, "link": link}))
        sent.append(TravelReminder.objects.create(booking=b, days_before=d, audience=TravelReminder.AGENT, ok=ok,
                                                  detail="" if ok else "No agent login to send to"))
    if cfg.remind_clients and not TravelReminder.objects.filter(booking=b, days_before=d, audience=TravelReminder.CLIENT).exists():
        from apps.integrations.messaging import send_message

        services.enable_portal(b)
        link = absolute(reverse("public_trip_portal", args=[b.portal_token]))
        first = b.client.full_name.split()[0] if b.client.full_name else "there"
        left = (b.travel_start - timezone.localdate()).days
        soon = "tomorrow" if left == 1 else ("today" if left == 0 else f"in {left} days, on {_when(b)}")
        needs = client_needs(b)
        text = (f"Hello {first}, your {trip} starts {soon}. Please have ready: {needs}.\n"
                f"Your full itinerary, payments and contacts: {link}\nWe look forward to hosting you!")
        detail = []
        if b.client.phone:
            m = send_message("whatsapp", b.client.phone, text, purpose="travel_client", related=f"booking:{b.pk}",
                             fields={"name": first, "trip": trip, "date": _when(b), "needs": needs, "link": link})
            detail.append(f"WhatsApp {m.status}{': ' + m.error if m.error else ''}")
        if b.client.email:
            m = send_message("email", b.client.email, text, subject=f"Your trip starts {soon}", purpose="travel_client", related=f"booking:{b.pk}")
            detail.append(f"e-mail {m.status}")
        sent.append(TravelReminder.objects.create(booking=b, days_before=d, audience=TravelReminder.CLIENT, ok=bool(detail) and "failed" not in " ".join(detail),
                                                  detail=("; ".join(detail) or "The client has no phone or e-mail")[:240]))
    return sent


def run(now=None) -> int:
    now = timezone.localtime(now or timezone.now())
    if now.hour < _settings().reminder_hour:
        return 0
    return sum(len(send_for(b, d)) for b, d in due(now.date()))
