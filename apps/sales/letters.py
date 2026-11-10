"""Standard letters filled in from a booking. Tokens are replaced by plain text substitution, never evaluated, so a template cannot run anything."""
import re

from django.utils import timezone

TOKENS = {
    "client_name": "Client's full name", "first_name": "Client's first name", "booking_ref": "Booking reference", "trip_title": "Trip title", "travel_dates": "Travel dates",
    "party": "Who is travelling", "total": "Trip total", "paid": "Amount paid", "balance": "Balance due", "deposit": "Deposit amount", "agent": "Your consultant's name",
    "agent_phone": "Consultant's phone", "company": "Company name", "company_phone": "Company phone", "today": "Today's date",
}
DEFAULTS = [
    ("Booking confirmation", "Your safari is confirmed: {booking_ref}",
     "Dear {first_name},\n\nThank you for choosing {company}. We are delighted to confirm your booking {booking_ref}: {trip_title}, {travel_dates}, for {party}.\n\nTrip total: {total}\nPaid so far: {paid}\nBalance: {balance}\n\nYour consultant {agent} ({agent_phone}) will be in touch with the final details. We cannot wait to host you.\n\nWarm regards,\n{company}"),
    ("Payment reminder", "Reminder: balance for {booking_ref}",
     "Dear {first_name},\n\nA friendly reminder that the balance of {balance} for your trip {trip_title} ({booking_ref}) is due before you travel on {travel_dates}.\n\nPlease use {booking_ref} as the payment reference and send us the confirmation message. If you have already paid, thank you, and please ignore this note.\n\nKind regards,\n{agent}\n{company}"),
    ("Thank you and review request", "Thank you, {first_name}",
     "Dear {first_name},\n\nIt was a pleasure hosting you on {trip_title}. We hope you came home with wonderful memories.\n\nIf you enjoyed the trip, a short review would mean a great deal to a small team like ours, and you are always welcome back.\n\nWith thanks,\n{agent}\n{company}"),
    ("Cancellation notice", "Your booking {booking_ref} has been cancelled",
     "Dear {first_name},\n\nThis confirms that booking {booking_ref} ({trip_title}) has been cancelled as requested. Any refund due is handled under the cancellation terms you were sent with your itinerary, and we will write to you about it separately.\n\nWe are sorry we will not be hosting you this time.\n\nKind regards,\n{agent}\n{company}"),
]
TOKEN_RE = re.compile(r"\{(\w+)\}")


def context(booking, company) -> dict:
    cur = booking.currency
    agent = booking.agent
    dates = ""
    if booking.travel_start:
        dates = f"{booking.travel_start:%d %b %Y}" + (f" to {booking.travel_end:%d %b %Y}" if booking.travel_end else "")
    return {"client_name": booking.client.full_name, "first_name": booking.client.full_name.split()[0], "booking_ref": booking.reference, "trip_title": booking.title,
            "travel_dates": dates or "dates to be confirmed", "party": booking.party_label, "total": f"{cur} {booking.total_amount:,.0f}", "paid": f"{cur} {booking.amount_paid:,.0f}",
            "balance": f"{cur} {booking.balance:,.0f}", "deposit": f"{cur} {booking.deposit_amount:,.0f}", "agent": agent.full_name if agent else company.name,
            "agent_phone": (agent.phone if agent and agent.phone else company.phone) or "", "company": company.name, "company_phone": company.phone or "",
            "today": f"{timezone.localdate():%d %B %Y}"}


def merge(text: str, ctx: dict) -> str:
    return TOKEN_RE.sub(lambda m: str(ctx.get(m.group(1), m.group(0))), text or "")
