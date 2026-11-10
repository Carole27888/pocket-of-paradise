"""The client's trip page: what is paid, what is due, the itinerary, what to bring, and a rating once the trip is over."""
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

DOCUMENTS = [
    "Passport valid for at least 6 months after you travel, with 2 blank pages",
    "Kenya eTA approved before you fly (apply online at www.etakenya.go.ke)",
    "Return flight ticket and travel insurance details",
    "Yellow fever certificate if you arrive from a risk country",
    "A printed or saved copy of your booking and this page",
]
PACKING = [
    "Light, neutral clothes (khaki, olive, beige) and a warm layer for early drives",
    "Comfortable closed shoes and sandals; a hat and sunglasses",
    "Sunscreen, insect repellent, and any personal medicine",
    "Binoculars and a camera with spare batteries or power bank",
    "Small bag for day trips and a refillable water bottle",
    "Some cash in small notes for tips and curios",
]


def lines(text: str) -> list:
    return [l.strip() for l in (text or "").splitlines() if l.strip()]


def itinerary_for(booking):
    """The itinerary behind this booking: the one its quotation was made from, else the last one sent to the client, else the newest."""
    its = booking.enquiry.itineraries.all()
    if booking.quotation_id:
        hit = its.filter(quotation_id=booking.quotation_id).first()
        if hit:
            return hit
    return its.filter(status="sent").first() or its.first()


def trip_over(booking) -> bool:
    end = booking.travel_end or booking.travel_start
    return bool(end and end < timezone.localdate()) or booking.status == "completed"


def payment_rows(booking):
    """What is due, in order: the payment plan's instalments if there is one, else the deposit and the balance."""
    plan = getattr(booking, "plan", None)
    rows = []
    if plan is not None and plan.instalments.exists():
        for i in plan.instalments.all():
            rows.append({"label": f"Instalment due {i.due_date:%d %b %Y}", "amount": i.amount, "paid": i.paid_amount, "state": i.state})
        return rows
    dep = booking.deposit_amount
    paid_dep = min(booking.amount_paid, dep)
    rows.append({"label": f"Deposit ({booking.deposit_percent}%)", "amount": dep, "paid": paid_dep, "state": "paid" if paid_dep >= dep else "upcoming"})
    rest = booking.total_amount - dep
    rows.append({"label": "Balance" + (f", due by {booking.travel_start - timedelta(days=30):%d %b %Y}" if booking.travel_start else ""), "amount": rest,
                 "paid": max(booking.amount_paid - dep, Decimal("0")), "state": "paid" if booking.balance <= 0 else "upcoming"})
    return rows
