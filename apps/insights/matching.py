"""Rules over the company's own data, with the evidence shown: package matching, next best action and customer-care flags."""
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from apps.leads.models import OPEN_STAGES, Enquiry, Stage


# ------------------------------------------------------------------ which safari suits this lead
def match_packages(e: Enquiry, limit=4) -> list:
    from apps.catalog.models import TourPackage
    from apps.sales.services import fx_rate

    pkgs = TourPackage.objects.alive().filter(status__in=["published", "seasonal"]).prefetch_related("destinations")
    interest = (e.interest_label or "").lower() + " " + (e.destination_text or "").lower() + " " + (e.notes or "").lower()
    words = {w for w in interest.replace(",", " ").split() if len(w) > 3}
    pax = e.adults + e.children
    out = []
    for p in pkgs:
        score, why, concerns = 0, [], []
        dests = {d.pk: d.name for d in p.destinations.all()}
        if e.package_id == p.pk:
            score += 25
            why.append("It is the package they asked about")
        if e.destination_id and e.destination_id in dests:
            score += 35
            why.append(f"Visits {dests[e.destination_id]}, their chosen destination")
        text = f"{p.title} {p.summary} {' '.join(dests.values())}".lower()
        hits = sorted(w for w in words if w in text)
        if hits:
            score += min(15, 5 * len(hits))
            why.append("Matches what they wrote: " + ", ".join(hits[:3]))
        price_kes = p.price_per_person * fx_rate(p.currency)
        if e.budget_max_pp:
            cap = e.budget_max_pp * fx_rate(e.budget_currency)
            if price_kes <= cap:
                score += 25
                why.append(f"Within budget (from KES {price_kes:,.0f} per person)")
            elif price_kes <= cap * Decimal("1.15"):
                score += 8
                concerns.append(f"Slightly above budget (KES {price_kes:,.0f} per person)")
            else:
                score -= 10
                concerns.append(f"Above budget (KES {price_kes:,.0f} per person)")
        if p.min_group <= pax <= p.max_group:
            score += 8
            why.append(f"Suits a party of {pax}")
        elif pax > p.max_group:
            concerns.append(f"Party of {pax} is over the usual group size of {p.max_group}: needs a second vehicle")
        if score > 0:
            out.append({"package": p, "score": min(score, 100), "why": why, "concerns": concerns, "price_kes": price_kes})
    return sorted(out, key=lambda r: -r["score"])[:limit]


# ------------------------------------------------------------------ the next step on a lead
def next_action(e: Enquiry) -> dict:
    from apps.sales.models import Booking, BookingStatus, FollowUp
    from apps.sales.services import SalesSettings

    now = timezone.now()
    sla = SalesSettings.get().response_sla_minutes
    stage = e.stage
    if stage in (Stage.LOST, Stage.COMPLETED):
        return {"action": "Nothing to do", "urgency": "low", "reason": "This lead is closed." if stage == Stage.COMPLETED else "This lead was lost."}
    if not e.assigned_agent_id:
        return {"action": "Hand it to an agent", "urgency": "high", "reason": "It is waiting at the Front Desk with no agent yet.", "who": "Front Desk"}
    if e.last_contact_at is None:
        mins = int((now - (e.created_at)).total_seconds() // 60)
        return {"action": "Call or WhatsApp them now", "urgency": "high" if mins > sla else "medium", "reason": f"No one has contacted them yet ({mins} minutes since the enquiry; target {sla})."}
    late = FollowUp.objects.filter(enquiry=e, done_at__isnull=True, due_at__lt=now).order_by("due_at").first()
    if late:
        return {"action": late.title, "urgency": "high", "reason": f"A follow-up was due {timezone.localtime(late.due_at):%d %b %H:%M}."}
    days = (now - e.last_contact_at).days
    b = Booking.objects.filter(enquiry=e).exclude(status=BookingStatus.CANCELLED).first()
    if b:
        if b.status == BookingStatus.PENDING:
            return {"action": "Ask for the deposit", "urgency": "high" if days >= 2 else "medium", "reason": f"The deposit of {b.currency} {b.deposit_amount:,.0f} on {b.reference} has not been paid."}
        if b.balance > 0:
            tr = f" Travel starts {b.travel_start:%d %b}." if b.travel_start else ""
            return {"action": "Collect the balance", "urgency": "medium", "reason": f"{b.currency} {b.balance:,.0f} is still due on {b.reference}.{tr}"}
        return {"action": "Prepare the trip", "urgency": "low", "reason": "Fully paid. Check documents, rooming and the departure."}
    if stage == Stage.ENQUIRY:
        return {"action": "Send a quotation", "urgency": "medium", "reason": "They have been contacted but have no quotation yet."}
    if stage == Stage.QUOTED:
        return {"action": "Follow up on the quotation", "urgency": "high" if days >= 3 else "medium", "reason": f"Quotation sent; last contact {days} day(s) ago."}
    if stage == Stage.HOT:
        return {"action": "Send the itinerary and ask for a decision", "urgency": "high", "reason": "Marked hot: strike while they are interested."}
    if stage == Stage.ITINERARY:
        return {"action": "Follow up and ask for the deposit", "urgency": "high" if days >= 3 else "medium", "reason": f"Itinerary sent; last contact {days} day(s) ago."}
    return {"action": "Keep in touch", "urgency": "low", "reason": "Nothing is overdue."}


def draft_message(e: Enquiry, action: str) -> dict:
    """A short WhatsApp message for the agent to check and send. AI when a key is saved, else a template."""
    first = e.client.full_name.split(" ")[0]
    plain = (f"Hello {first}, this is {e.assigned_agent.short_name if e.assigned_agent_id else 'your travel consultant'} from Pocket of Paradise. "
             f"I'm following up on your {e.interest_label} enquiry. {action}? Happy to answer any questions or adjust anything.")
    try:
        from apps.ict import ai

        if ai.configured():
            resp = ai.complete("Write one short, friendly WhatsApp message (max 60 words) from a safari sales agent. Use only the facts given. No emojis overload, no promises about price or availability.",
                               [{"role": "user", "content": f"Client first name: {first}\nInterest: {e.interest_label}\nParty: {e.party_label}\nStage: {e.get_stage_display()}\nPurpose of message: {action}"}], max_tokens=200, feature="matching")
            t = ai.text_of(resp)
            if t:
                return {"text": t, "ai": True}
    except Exception:
        pass
    return {"text": plain, "ai": False}


# ------------------------------------------------------------------ customer care flags
def care_flags(qs=None) -> list:
    """Clients at risk of poor service, each with the reason. Agents see their own leads; managers see all."""
    from apps.sales.models import FollowUp, Quotation, QuotationStatus
    from apps.sales.services import SalesSettings

    now = timezone.now()
    sla = SalesSettings.get().response_sla_minutes
    qs = (qs if qs is not None else Enquiry.objects.all()).filter(stage__in=OPEN_STAGES).select_related("client", "assigned_agent")[:400]
    flags = []
    for e in qs:
        f = []
        if e.assigned_agent_id is None and now - e.created_at > timedelta(minutes=sla):
            f.append(("danger", f"Unanswered for {int((now - e.created_at).total_seconds() // 3600)}h: nobody is assigned"))
        elif e.last_contact_at is None and now - e.created_at > timedelta(minutes=sla):
            f.append(("danger", f"First contact is {int((now - e.created_at).total_seconds() // 60)} minutes late"))
        elif e.last_contact_at and now - e.last_contact_at > timedelta(days=5):
            f.append(("warning", f"Gone silent: no contact for {(now - e.last_contact_at).days} days"))
        overdue = FollowUp.objects.filter(enquiry=e, done_at__isnull=True, due_at__lt=now - timedelta(hours=4)).count()
        if overdue:
            f.append(("warning", f"{overdue} promised follow-up{'s' if overdue != 1 else ''} missed"))
        if Quotation.objects.filter(enquiry=e, status=QuotationStatus.SENT, sent_at__lt=now - timedelta(days=4)).exists():
            f.append(("info", "Quotation waiting for an answer for 4+ days"))
        if f:
            flags.append({"e": e, "flags": f, "level": "danger" if any(l == "danger" for l, _ in f) else "warning"})
    return sorted(flags, key=lambda r: (r["level"] != "danger", -len(r["flags"])))
