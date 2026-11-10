"""The travel-day preview: one page with everything about a client's trip (client and party, itinerary, flights, lodges, the
departure's guide, driver and vehicle, documents, payments and contacts). The agent's travel reminder links here."""
from django import forms
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.permissions import module_required
from apps.core.forms import StyledModelForm

from . import portal, services
from .models import BookingFlight
from .views import _booking

SALES = "sales"


class FlightForm(StyledModelForm):
    class Meta:
        model = BookingFlight
        fields = ["leg", "airline", "flight_number", "from_airport", "to_airport", "departs_at", "arrives_at", "pnr", "notes"]
        widgets = {"departs_at": forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
                   "arrives_at": forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M")}


def _documents(b):
    """What paperwork exists for the trip and its state: ID check, signed documents, invoices."""
    from apps.core.models import CompanySetting
    from apps.dealdesk.models import ClientKYC, SignatureRequest
    from apps.finance.models import Invoice

    out = []
    kyc = ClientKYC.objects.filter(client=b.client).first()
    out.append({"label": "ID / passport check (KYC)", "ok": bool(kyc and kyc.status == ClientKYC.VERIFIED), "detail": kyc.get_status_display() if kyc else "Not started"})
    for s in SignatureRequest.objects.filter(booking=b)[:3]:
        out.append({"label": f"Signed: {s.title}", "ok": s.status == SignatureRequest.SIGNED, "detail": s.get_status_display()})
    for inv in Invoice.objects.filter(booking=b).exclude(status="void").order_by("-pk")[:4]:
        out.append({"label": f"Invoice {inv.number}", "ok": inv.status == "paid", "detail": inv.get_status_display()})
    return out, (portal.lines(CompanySetting.load().documents_list) or portal.DOCUMENTS)


@module_required(SALES)
def travel_day(request, pk):
    from apps.core.models import CompanySetting

    b = _booking(request, pk)
    it = portal.itinerary_for(b)
    days = list(it.days.select_related("destination", "accommodation")) if it else []
    stays = []
    for d in days:
        if d.accommodation and d.accommodation.name not in [s["name"] for s in stays]:
            stays.append({"name": d.accommodation.name, "acc": d.accommodation, "nights": sum(1 for x in days if x.accommodation_id == d.accommodation_id),
                          "first": d.date, "room": d.room_type})
    docs, client_docs = _documents(b)
    party = list(b.enquiry.party_members.all())
    left = (b.travel_start - timezone.localdate()).days if b.travel_start else None
    return render(request, "sales/travel_day.html", {
        "b": b, "it": it, "days": days, "stays": stays, "flights": b.flights.all(), "dep": b.departure, "payments": b.payments.all(),
        "rows": portal.payment_rows(b), "docs": docs, "client_docs": client_docs, "party": party, "left": left, "company": CompanySetting.load(),
        "flight_form": FlightForm(), "can_act": services.can_act_on(request.user, b.enquiry),
        "reminders": b.travel_reminders.all()})


@require_POST
@module_required(SALES)
def flight_add(request, pk):
    b = _booking(request, pk, act=True)
    form = FlightForm(request.POST)
    if form.is_valid():
        f = form.save(commit=False)
        f.booking = b
        f.save()
        messages.success(request, f"Flight {f} added. The client sees it on their trip page.")
    else:
        messages.error(request, "Flight not added: " + "; ".join(f"{form.fields[k].label}: {' '.join(v)}" for k, v in form.errors.items()))
    return redirect("sales:booking_travel_day", pk=b.pk)


@require_POST
@module_required(SALES)
def flight_delete(request, pk, fid):
    b = _booking(request, pk, act=True)
    get_object_or_404(b.flights.all(), pk=fid).delete()
    messages.success(request, "Flight removed.")
    return redirect("sales:booking_travel_day", pk=b.pk)
