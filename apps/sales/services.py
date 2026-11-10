"""Sales business logic: scoping, assignment, quotations, bookings and payments."""
from datetime import date, timedelta
import re
from decimal import Decimal

from django.db import transaction
from django.db.models import Count, F, Max, Q
from django.db.models.functions import Coalesce
from django.utils import timezone

from apps.accounts.permissions import is_scoped_agent
from apps.core.models import Currency, ExchangeRate
from apps.leads import services as lead_services
from apps.leads.models import OPEN_STAGES, Enquiry, LeadActivity, LeadAssignment, Stage
from apps.people.models import Employee

from .models import (Booking, BookingPayment, BookingStatus, CallLog, FollowUp, Quotation, QuotationItem,
                     QuotationStatus, SalesSettings, q2)

# Probability a deal in each stage closes; drives the weighted forecast.
STAGE_WEIGHT = {"enquiry": 10, "quoted": 25, "hot": 60, "itinerary": 70, "deposit": 90, "confirmed": 100}
# Columns on the board and whether a card may be dropped there by hand.
BOARD_STAGES = [Stage.ENQUIRY, Stage.QUOTED, Stage.HOT, Stage.ITINERARY, Stage.DEPOSIT, Stage.CONFIRMED]
DROPPABLE = {Stage.ENQUIRY, Stage.QUOTED, Stage.HOT, Stage.ITINERARY, Stage.LOST}
QUOTE_STAGES = {Stage.QUOTED, Stage.HOT, Stage.ITINERARY}   # a person may only move a lead here once a quotation exists for it


def quote_needed(enquiry, stage) -> bool:
    """True when `stage` means a quotation has been given to the client but this lead has none on record."""
    return Stage(stage) in QUOTE_STAGES and enquiry.stage != Stage(stage) and not enquiry.quotations.exists()


def quote_prompt(enquiry, stage) -> dict:
    """What the screen needs to ask the user: the lead, the stage they wanted, and where to go next.
    A quotation is made from an itinerary, so a lead with no itinerary is sent to build one first."""
    from django.urls import reverse

    base = {"need_quote": True, "name": enquiry.client.full_name, "reference": enquiry.reference, "stage": Stage(stage).label}
    if not enquiry.itineraries.exists():
        return {**base, "no_itinerary": True, "title": "Build the itinerary first",
                "message": f"{enquiry.client.full_name} ({enquiry.reference}) has no quotation yet, and a quotation is made from an itinerary. Build the itinerary, then turn it into the quotation.",
                "button": "Build itinerary", "url": reverse("sales:itinerary_new") + f"?enquiry={enquiry.pk}"}
    return {**base, "title": "Create the quotation first",
            "message": f"{enquiry.client.full_name} ({enquiry.reference}) has no quotation yet, so the lead cannot move to {Stage(stage).label}.",
            "button": "Create quotation", "url": reverse("sales:quotation_new") + f"?enquiry={enquiry.pk}"}


# ------------------------------------------------------------------------------------------------ scoping
def agent_of(user) -> Employee | None:
    return getattr(user, "employee", None)


def visible_enquiries(user):
    from apps.leads.views import visible_enquiries as _v

    return _v(user)


def _scope(qs, user, path="enquiry__assigned_agent__user"):
    return qs.filter(**{path: user}) if is_scoped_agent(user) else qs


def quotations_for(user):
    return _scope(Quotation.objects.select_related("enquiry__client", "agent", "enquiry__assigned_agent"), user)


def bookings_for(user):
    return _scope(Booking.objects.select_related("client", "agent", "enquiry", "enquiry__brought_by", "enquiry__captured_by", "package")
                  .prefetch_related("payments"), user)


def calls_for(user):
    return _scope(CallLog.objects.select_related("enquiry__client", "agent"), user)


def followups_for(user):
    return _scope(FollowUp.objects.select_related("enquiry__client", "agent"), user)


def can_act_on(user, enquiry: Enquiry) -> bool:
    """Create calls/quotes/tasks on an enquiry: managers any, agents their own; directors never."""
    if not user.can_work_sales:
        return False
    if user.can_manage_sales:
        return True
    return bool(enquiry.assigned_agent_id and enquiry.assigned_agent.user_id == user.pk)


# ------------------------------------------------------------------------------------------------ money
def fx_rate(currency: str) -> Decimal:
    if currency == "KES":
        return Decimal("1")
    try:
        cur = Currency.objects.get(code=currency)
        rate = ExchangeRate.objects.filter(currency=cur).order_by("-date").first()
        if rate:
            return rate.rate_to_base
    except Currency.DoesNotExist:
        pass
    return {"USD": Decimal("129"), "EUR": Decimal("140"), "GBP": Decimal("165")}.get(currency, Decimal("1"))


def sum_kes(qs, amount="total_amount", rate="fx_rate") -> Decimal:
    from django.db.models import DecimalField, ExpressionWrapper, Sum

    expr = ExpressionWrapper(F(amount) * F(rate), output_field=DecimalField(max_digits=18, decimal_places=2))
    return qs.aggregate(t=Sum(expr))["t"] or Decimal("0")


# ------------------------------------------------------------------------------------------------ assignment
def available_agents():
    today = timezone.localdate()
    load = dict(LeadAssignment.objects.filter(assigned_at__date=today).values_list("agent").annotate(n=Count("pk")))
    return [a for a in Employee.objects.filter(is_sales_agent=True, is_active=True)
            if a.is_available and load.get(a.pk, 0) < a.daily_enquiry_limit]


def pick_agent(enquiry: Enquiry, exclude=()):
    """(agent, method, why) for the enquiry: first matching rule, else the agent who waited longest (round robin)."""
    from .models import AssignmentRule

    ok = {a.pk: a for a in available_agents() if a.pk not in exclude}
    if not ok:
        return None, "", ""
    owner_id = enquiry.client.owner_id
    if owner_id in ok:  # a returning client goes back to the agent who owns them
        return ok[owner_id], "owner", "Client owner"
    for rule in AssignmentRule.objects.filter(active=True).select_related("agent", "destination"):
        if rule.agent_id in ok and rule.matches(enquiry):
            return ok[rule.agent_id], "rule", rule.name
    pool, why, method = ok, "Round robin", "round_robin"
    if SalesSettings.get().route_by_specialty:
        special = specialists_for(enquiry, ok.values())
        if special:
            pool, why, method = {a.pk: a for a in special}, "Specialist: " + ", ".join(sorted(matched_terms(enquiry, special[0]))[:2]), "rule"
    last = dict(LeadAssignment.objects.filter(agent_id__in=pool).values_list("agent").annotate(m=Max("assigned_at")))
    epoch = timezone.make_aware(timezone.datetime(2000, 1, 1))
    agent = min(pool.values(), key=lambda a: (last.get(a.pk) or epoch, a.pk))
    return agent, method, why


def _interest_text(enquiry) -> str:
    d = enquiry.destination
    bits = [enquiry.destination_text or "", getattr(enquiry, "notes", "") or "", enquiry.get_purpose_display() if enquiry.purpose else ""]
    if d:
        bits += [d.name, d.get_region_display()]
    return " ".join(bits).lower()


def matched_terms(enquiry, agent) -> set:
    text = _interest_text(enquiry)
    out = set()
    for sp in agent.specialties.all():
        name = sp.name.lower()
        words = [w for w in re.split(r"[^a-z]+", name) if len(w) > 3]
        if name in text or any(w in text for w in words):
            out.add(sp.name)
    return out


def specialists_for(enquiry, agents) -> list:
    """Available agents whose specialties match what the client asked for, best match first."""
    scored = [(len(matched_terms(enquiry, a)), a) for a in agents]
    scored = [(n, a) for n, a in scored if n]
    if not scored:
        return []
    best = max(n for n, _ in scored)
    return [a for n, a in scored if n == best]


def auto_assign(enquiry: Enquiry, user=None):
    agent, method, why = pick_agent(enquiry)
    if agent is None:
        raise ValueError("No sales agent is available right now.")
    lead_services.assign_agent(enquiry, agent, user, method, f"Auto: {why}")
    return agent, why


def maybe_auto_assign(enquiry: Enquiry, user=None):
    if enquiry.assigned_agent_id or not SalesSettings.get().auto_assign_new:
        return None
    try:
        return auto_assign(enquiry, user)
    except ValueError:
        return None


def auto_assign_unassigned(user=None, limit=200):
    done, skipped = 0, 0
    qs = (Enquiry.objects.filter(assigned_agent__isnull=True, stage__in=OPEN_STAGES)
          .select_related("client", "destination").order_by("created_at")[:limit])
    for enquiry in qs:
        try:
            auto_assign(enquiry, user)
            done += 1
        except ValueError:
            skipped += 1
            break  # nobody available: stop rather than loop through all
    return done, skipped


def pick_owner():
    """Next agent in the client rotation: the available agent who was given a client longest ago."""
    from apps.contacts.models import Client

    ok = available_agents()
    if not ok:
        return None
    last = dict(Client.objects.filter(owner_id__in=[a.pk for a in ok]).values_list("owner")
                .annotate(m=Max("owner_assigned_at")))
    epoch = timezone.make_aware(timezone.datetime(2000, 1, 1))
    return min(ok, key=lambda a: (last.get(a.pk) or epoch, a.pk))


def set_client_owner(client, agent, user=None, tell=True):
    """Make `agent` the client's owner (None clears it) and tell them (not when a new-lead alert already does)."""
    if client.owner_id == getattr(agent, "pk", None):
        return client
    client.owner = agent
    client.owner_assigned_at = timezone.now() if agent else None
    client.save(update_fields=["owner", "owner_assigned_at", "updated_at", "updated_by"])
    if tell and agent and agent.user_id and agent.user_id != getattr(user, "pk", None):
        from apps.notifications.services import notify

        transaction.on_commit(lambda: notify(
            agent.user, f"New client: {client.full_name}", "This client has been assigned to you.",
            client.get_absolute_url(), "info", dedupe_key=f"client-owner:{client.pk}:{agent.pk}"))
    return client


def sla_breaches(qs=None):
    """Open, never-contacted enquiries older than the response SLA (clock restarts on reassignment)."""
    cutoff = timezone.now() - timedelta(minutes=SalesSettings.get().response_sla_minutes)
    qs = qs if qs is not None else Enquiry.objects.all()
    return (qs.filter(stage=Stage.ENQUIRY, last_contact_at__isnull=True)
            .annotate(clock=Coalesce(Max("assignments__assigned_at"), F("created_at")))
            .filter(clock__lt=cutoff))


def going_cold(qs=None):
    days = SalesSettings.get().follow_up_stale_days
    cutoff = timezone.now() - timedelta(days=days)
    qs = qs if qs is not None else Enquiry.objects.all()
    return qs.filter(stage__in=[Stage.QUOTED, Stage.HOT, Stage.ITINERARY]).filter(
        Q(last_contact_at__lt=cutoff) | Q(last_contact_at__isnull=True, stage_changed_at__lt=cutoff))


# ------------------------------------------------------------------------------------------------ calls & tasks
@transaction.atomic
def log_call(enquiry: Enquiry, user, *, outcome, direction="out", duration_seconds=0, notes="", called_at=None,
             follow_up_at=None, follow_up_title=""):
    when = called_at or timezone.now()
    call = CallLog.objects.create(enquiry=enquiry, agent=enquiry.assigned_agent or agent_of(user), logged_by=user,
                                  direction=direction, outcome=outcome, duration_seconds=duration_seconds,
                                  notes=notes, called_at=when)
    lead_services.log_activity(enquiry, "call", f"{call.get_direction_display()} call · {call.get_outcome_display()}"
                               + (f" · {notes}" if notes else ""), user, when)
    if outcome == "not_interested" and enquiry.stage in OPEN_STAGES:
        enquiry.next_action = "Confirm lost reason"
        enquiry.save(update_fields=["next_action", "updated_at", "updated_by"])
    if follow_up_at:
        add_followup(enquiry, user, kind="call", title=follow_up_title or "Follow-up call", due_at=follow_up_at)
    elif SalesSettings.get().automation_on and SalesSettings.get().auto_callback_after_call and enquiry.stage in OPEN_STAGES \
            and not enquiry.followups.filter(done_at__isnull=True).exists():
        # nobody scheduled the next step: do it for them
        tomorrow = (timezone.localtime(when) + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
        if outcome == "interested" and not enquiry.quotations.exclude(status=QuotationStatus.DRAFT).exists():
            FollowUp.objects.create(enquiry=enquiry, agent=call.agent, kind="send_quote", title=f"Send {enquiry.client.full_name} the quotation (they were interested)",
                                    due_at=tomorrow, auto_key=f"call-next:{call.pk}")
            refresh_next_action(enquiry)
        elif outcome == "voicemail":
            FollowUp.objects.create(enquiry=enquiry, agent=call.agent, kind="call", title=f"Call {enquiry.client.full_name} back (left a voicemail)",
                                    due_at=tomorrow, auto_key=f"call-next:{call.pk}")
            refresh_next_action(enquiry)
    return call


def add_followup(enquiry: Enquiry, user, *, kind, title, due_at) -> FollowUp:
    fu = FollowUp.objects.create(enquiry=enquiry, agent=enquiry.assigned_agent or agent_of(user), kind=kind,
                                 title=title, due_at=due_at)
    refresh_next_action(enquiry)
    return fu


def refresh_next_action(enquiry: Enquiry):
    nxt = enquiry.followups.filter(done_at__isnull=True).order_by("due_at").first()
    Enquiry.objects.filter(pk=enquiry.pk).update(next_action=nxt.title if nxt else "",
                                                 next_action_due=nxt.due_at if nxt else None)


def complete_followup(fu: FollowUp, user):
    if not fu.done_at:
        fu.done_at = timezone.now()
        fu.save(update_fields=["done_at", "updated_at", "updated_by"])
        lead_services.log_activity(fu.enquiry, "note", f"Task done: {fu.title}", user)
        refresh_next_action(fu.enquiry)
    return fu


# ------------------------------------------------------------------------------------------------ quotations
def _sync_deal_value(enquiry: Enquiry, kes):
    Enquiry.objects.filter(pk=enquiry.pk).update(deal_value=kes)
    enquiry.deal_value = kes


@transaction.atomic
def create_quotation(enquiry: Enquiry, user, *, title="", currency=None, items=()) -> Quotation:
    cfg = SalesSettings.get()
    version = (enquiry.quotations.aggregate(m=Max("version"))["m"] or 0) + 1
    currency = currency or ("KES" if not enquiry.client.is_international else "USD")
    q = Quotation.objects.create(
        enquiry=enquiry, version=version, agent=enquiry.assigned_agent or agent_of(user),
        title=title or f"{enquiry.interest_label} · {enquiry.party_label}", currency=currency,
        fx_rate=fx_rate(currency), vat_rate=cfg.vat_rate if currency == "KES" else 0,
        valid_until=timezone.localdate() + timedelta(days=cfg.quote_valid_days),
        terms=standard_terms())
    for pos, it in enumerate(items):
        QuotationItem.objects.create(quotation=q, position=pos, **it)
    q.recalculate()
    return q


@transaction.atomic
def revise_quotation(old: Quotation, user) -> Quotation:
    """Copy into the next draft version (price changes never rewrite what the client was sent)."""
    version = (old.enquiry.quotations.aggregate(m=Max("version"))["m"] or 0) + 1
    new = Quotation.objects.create(
        enquiry=old.enquiry, version=version, agent=old.agent, title=old.title, intro=old.intro,
        currency=old.currency, fx_rate=fx_rate(old.currency), discount=old.discount, vat_rate=old.vat_rate,
        valid_until=timezone.localdate() + timedelta(days=SalesSettings.get().quote_valid_days), terms=old.terms)
    for it in old.items.all():
        QuotationItem.objects.create(quotation=new, kind=it.kind, description=it.description, quantity=it.quantity,
                                     unit_price=it.unit_price, position=it.position)
    new.recalculate()
    return new


@transaction.atomic
def send_quotation(q: Quotation, via: str, user) -> Quotation:
    if q.status != QuotationStatus.DRAFT:
        raise ValueError("Only a draft quotation can be sent.")
    if not q.items.exists():
        raise ValueError("Add at least one line before sending.")
    q.recalculate(save=False)
    q.status, q.sent_at, q.sent_via = QuotationStatus.SENT, timezone.now(), via
    q.save()
    enquiry = q.enquiry
    enquiry.quotations.filter(status=QuotationStatus.SENT).exclude(pk=q.pk).update(
        status=QuotationStatus.SUPERSEDED)
    enquiry.quotation_status = "revised" if q.version > 1 else "quoted"
    enquiry.save(update_fields=["quotation_status", "updated_at", "updated_by"])
    _sync_deal_value(enquiry, q.total_kes)
    if enquiry.stage == Stage.ENQUIRY:
        lead_services.change_stage(enquiry, Stage.QUOTED, user)
    lead_services.log_activity(enquiry, "quotation", f"{q.reference} v{q.version} sent by {via} · "
                               f"{q.currency} {q.total:,.0f}", user)
    return q


@transaction.atomic
def reject_quotation(q: Quotation, user, note="") -> Quotation:
    if q.status != QuotationStatus.SENT:
        raise ValueError("Only a sent quotation can be rejected.")
    q.status, q.decided_at, q.decision_note = QuotationStatus.REJECTED, timezone.now(), note
    q.save(update_fields=["status", "decided_at", "decision_note", "updated_at", "updated_by"])
    lead_services.log_activity(q.enquiry, "note", f"{q.reference} v{q.version} declined by client"
                               + (f": {note}" if note else ""), user)
    return q


@transaction.atomic
def accept_quotation(q: Quotation, user, *, travel_start=None, travel_end=None, deposit_percent=30) -> Booking:
    if q.status != QuotationStatus.SENT:
        raise ValueError("Send the quotation before the client can accept it.")
    enquiry = q.enquiry
    if hasattr(enquiry, "booking") and enquiry.booking.status != BookingStatus.CANCELLED:
        raise ValueError(f"{enquiry.reference} already has booking {enquiry.booking.reference}.")
    q.status, q.decided_at = QuotationStatus.ACCEPTED, timezone.now()
    q.save(update_fields=["status", "decided_at", "updated_at", "updated_by"])
    enquiry.quotations.filter(status__in=[QuotationStatus.SENT, QuotationStatus.DRAFT]).exclude(pk=q.pk).update(
        status=QuotationStatus.SUPERSEDED)
    booking = Booking.objects.filter(enquiry=enquiry).first()  # a cancelled booking is reopened as a new one
    fields = dict(quotation=q, client=enquiry.client, agent=enquiry.assigned_agent, package=enquiry.package,
                  title=q.title, travel_start=travel_start or enquiry.travel_date_from,
                  travel_end=travel_end or enquiry.travel_date_to, adults=enquiry.adults, children=enquiry.children,
                  currency=q.currency, fx_rate=q.fx_rate, total_amount=q.total, deposit_percent=deposit_percent,
                  status=BookingStatus.PENDING, booked_at=timezone.now(), cancelled_reason="")
    if booking:
        for k, v in fields.items():
            setattr(booking, k, v)
        booking.save()
    else:
        booking = Booking.objects.create(enquiry=enquiry, **fields)
    _sync_deal_value(enquiry, q.total_kes)
    from apps.finance.services import invoice_for_booking

    invoice_for_booking(booking, user)
    if enquiry.stage in (Stage.ENQUIRY, Stage.QUOTED, Stage.HOT, Stage.LOST):
        lead_services.change_stage(enquiry, Stage.ITINERARY, user)
    lead_services.log_activity(enquiry, "note", f"Booking {booking.reference} created from {q.reference} · "
                               f"{q.currency} {q.total:,.0f}", user)
    return booking


def standard_terms() -> str:
    """The payment & cancellation terms Operations has set (or the built-in default), filled into new itineraries and quotations."""
    from apps.core.models import DEFAULT_TERMS, CompanySetting

    return (CompanySetting.load().standard_terms or "").strip() or DEFAULT_TERMS


# ------------------------------------------------------------------------------------------------ bookings
@transaction.atomic
def record_payment(booking: Booking, user, *, amount, method="mpesa", reference="", received_at=None, note=""):
    if booking.status in (BookingStatus.CANCELLED, BookingStatus.COMPLETED):
        raise ValueError("This booking is closed.")
    amount = q2(amount)
    if amount <= 0:
        raise ValueError("Enter an amount above zero.")
    from apps.dealdesk.services import apply_payment_to_plan, check_deposit_allowed

    check_deposit_allowed(booking)
    if amount > booking.balance:
        raise ValueError(f"That is more than the balance ({booking.currency} {booking.balance:,.2f}).")
    pay = BookingPayment.objects.create(booking=booking, amount=amount, method=method, reference=reference,
                                        received_at=received_at or timezone.now(), recorded_by=user, note=note)
    booking.__dict__.pop("_paid", None)
    getattr(booking, "_prefetched_objects_cache", {}).pop("payments", None)  # views prefetch payments
    from apps.finance.services import invoice_for_booking, record_receipt

    invoice = invoice_for_booking(booking, user)
    record_receipt(invoice, amount=amount, method=method if method != "other" else "bank", reference=reference,
                   received_at=pay.received_at, note=note, user=user)
    apply_payment_to_plan(booking, amount)
    enquiry = booking.enquiry
    lead_services.log_activity(enquiry, "note", f"Payment {booking.currency} {amount:,.0f} via "
                               f"{pay.get_method_display()}" + (f" ({reference})" if reference else ""), user)
    new_status = booking.status
    if booking.balance <= 0:
        new_status = BookingStatus.CONFIRMED
    elif booking.amount_paid >= booking.deposit_amount and booking.status == BookingStatus.PENDING:
        new_status = BookingStatus.DEPOSIT
    if new_status != booking.status:
        set_booking_status(booking, new_status, user)
    return pay


STATUS_TO_STAGE = {BookingStatus.DEPOSIT: Stage.DEPOSIT, BookingStatus.CONFIRMED: Stage.CONFIRMED,
                   BookingStatus.COMPLETED: Stage.COMPLETED}


@transaction.atomic
def set_booking_status(booking: Booking, status: str, user, reason: str = "") -> Booking:
    if status not in BookingStatus.values:
        raise ValueError("Unknown status")
    if status == booking.status:
        return booking
    if status == BookingStatus.COMPLETED and booking.status != BookingStatus.CONFIRMED:
        raise ValueError("Only a confirmed booking can be completed.")
    if status == BookingStatus.CONFIRMED and booking.amount_paid < booking.deposit_amount:
        raise ValueError("Record the deposit before confirming the booking.")
    if status == BookingStatus.CANCELLED and not reason:
        raise ValueError("Give a cancellation reason.")
    booking.status = status
    if status == BookingStatus.CANCELLED:
        booking.cancelled_reason = reason
    booking.save(update_fields=["status", "cancelled_reason", "updated_at", "updated_by"])
    if status == BookingStatus.COMPLETED:
        from apps.finance.services import recognise_booking_revenue

        recognise_booking_revenue(booking, user)
    enquiry = booking.enquiry
    if status == BookingStatus.CANCELLED:
        lead_services.change_stage(enquiry, Stage.LOST, user, f"Booking cancelled: {reason}")
    elif status in STATUS_TO_STAGE:
        lead_services.change_stage(enquiry, STATUS_TO_STAGE[status], user)
    return booking


# ------------------------------------------------------------------------------------------------ double allocation
def _activity_score(enquiry: Enquiry) -> tuple:
    """Who should keep the guest: contacted first, then most activity."""
    first = (enquiry.activities.filter(activity_type__in=list(LeadActivity.CONTACT_TYPES))
             .order_by("timestamp").values_list("timestamp", flat=True).first())
    return (first or timezone.now() + timedelta(days=3650), -enquiry.activities.count())


def conflicts():
    """[(client, [open enquiries])] where more than one agent actively holds the same guest."""
    from apps.contacts.models import Client

    out = []
    qs = (Enquiry.objects.filter(stage__in=OPEN_STAGES, assigned_agent__isnull=False)
          .values("client").annotate(agents=Count("assigned_agent", distinct=True)).filter(agents__gt=1))
    for row in qs:
        client = Client.objects.get(pk=row["client"])
        enquiries = list(client.enquiries.filter(stage__in=OPEN_STAGES, assigned_agent__isnull=False)
                         .select_related("assigned_agent"))
        out.append((client, enquiries))
    return out


@transaction.atomic
def detect_double_allocations() -> int:
    from .models import DoubleAllocation

    n = 0
    live = {c.pk for c, _ in conflicts()}
    for client, enquiries in conflicts():
        _, created = DoubleAllocation.objects.get_or_create(client=client, status=DoubleAllocation.OPEN)
        Enquiry.objects.filter(pk__in=[e.pk for e in enquiries]).update(double_allocated=True)
        n += created
    # conflicts that disappeared on their own are closed automatically
    for da in DoubleAllocation.objects.filter(status=DoubleAllocation.OPEN).exclude(client_id__in=live):
        da.status, da.reason, da.resolved_at = DoubleAllocation.RESOLVED, "Resolved on its own", timezone.now()
        da.save()
    Enquiry.objects.filter(double_allocated=True).exclude(client_id__in=live).update(double_allocated=False)
    return n


def suggested_owner(client):
    enquiries = list(client.enquiries.filter(stage__in=OPEN_STAGES, assigned_agent__isnull=False)
                     .select_related("assigned_agent"))
    if not enquiries:
        return None
    return min(enquiries, key=_activity_score).assigned_agent


@transaction.atomic
def resolve_double_allocation(da, keep_agent, user=None, reason=""):
    if da.status == da.RESOLVED:
        raise ValueError("Already resolved.")
    enquiries = list(da.client.enquiries.filter(stage__in=OPEN_STAGES, assigned_agent__isnull=False))
    agents = {e.assigned_agent_id for e in enquiries}
    if keep_agent.pk not in agents:
        raise ValueError(f"{keep_agent} is not one of the consultants holding this guest.")
    for e in enquiries:
        if e.assigned_agent_id != keep_agent.pk:
            previous = e.assigned_agent
            lead_services.assign_agent(e, keep_agent, user, "manual", f"Double allocation: kept {keep_agent.short_name}")
            if previous.user_id:
                from apps.notifications.services import notify

                transaction.on_commit(lambda p=previous, e=e: notify(
                    p.user, f"{e.reference} moved to {keep_agent.short_name}",
                    "The same guest was being worked by two consultants.", e.get_absolute_url(), "info",
                    dedupe_key=f"double:{e.pk}:{keep_agent.pk}"))
    da.status, da.kept_agent, da.reason = da.RESOLVED, keep_agent, reason or "Resolved by manager"
    da.resolved_by, da.resolved_at = user, timezone.now()
    da.save()
    Enquiry.objects.filter(client=da.client).update(double_allocated=False)
    return da


# ------------------------------------------------------------------------------------------------ itinerary builder
def suggest_rate(accommodation, on_date=None, currency="USD", room_type=""):
    """Lodge rate per person per night from the catalogue, converted to `currency`: (amount, room_type) or (None, '')."""
    from apps.catalog.models import RoomRate

    rates = list(RoomRate.objects.filter(accommodation=accommodation))
    if not rates:
        return None, ""
    if room_type:
        match = [r for r in rates if r.room_type.lower() == room_type.lower()]
        rates = match or rates
    pick = None
    if on_date:
        dated = [r for r in rates if (not r.valid_from or r.valid_from <= on_date) and (not r.valid_to or r.valid_to >= on_date)]
        pick = dated[0] if dated else None
    pick = pick or sorted(rates, key=lambda r: r.valid_to or date.max, reverse=True)[0]
    amount = pick.rate_per_person
    if pick.currency != currency:
        amount = q2(amount * fx_rate(pick.currency) / fx_rate(currency))
    return q2(amount), pick.room_type


def copy_day_image(src, dst):
    """Give `dst` its own copy of `src`'s picture (own upload, else destination photo), so deleting one never breaks the other."""
    from django.core.files.base import ContentFile

    f = src.picture_file
    if not f:
        return
    try:
        with f.open("rb") as fh:
            data = fh.read()
    except (FileNotFoundError, ValueError):
        return
    import os

    dst.image.save(os.path.basename(f.name), ContentFile(data), save=False)


@transaction.atomic
def itinerary_from_template(enquiry: Enquiry, user, template, *, title="") -> "LeadItinerary":
    from .models import LeadItinerary, LeadItineraryDay

    currency = "KES" if not enquiry.client.is_international else "USD"
    it = LeadItinerary.objects.create(enquiry=enquiry, title=title or f"{enquiry.client.full_name} · {enquiry.interest_label}", currency=currency, start_date=enquiry.travel_date_from,
                                      intro=template.intro or template.description, inclusions=template.inclusions, exclusions=template.exclusions,
                                      terms=template.terms or standard_terms(), nights_override=template.nights_override, created_by=user)
    for pl in template.price_lines.all():
        it.price_lines.create(description=pl.description, quantity=pl.quantity, unit_price=pl.unit_price)
    for d in template.days.select_related("accommodation", "destination"):
        lodging = d.lodging_pp
        room = d.room_type
        if d.accommodation_id:
            amt, r = suggest_rate(d.accommodation, it.start_date + timedelta(days=d.day_number - 1) if it.start_date else None, currency, room)
            if amt is not None:
                lodging, room = amt, r or room
        day = LeadItineraryDay(itinerary=it, day_number=d.day_number, title=d.title, description=d.description, destination=d.destination, accommodation=d.accommodation,
                               room_type=room, breakfast=d.breakfast, lunch=d.lunch, dinner=d.dinner, activities=d.activities, drive_km=d.drive_km,
                               drive_hours=d.drive_hours, lodging_pp=lodging, activities_pp=d.activities_pp, transport=d.transport)
        if d.image:
            copy_day_image(d, day)
        day.save()
    if not it.days.exists():
        LeadItineraryDay.objects.create(itinerary=it, day_number=1, title="Arrival and transfer")
    type(template).objects.filter(pk=template.pk).update(times_used=template.times_used + 1)
    return it


@transaction.atomic
def template_from_itinerary(it, user, title="") -> "ItineraryTemplate":
    """Save a finished itinerary as a team template (days, pictures and prices; nothing about the client)."""
    from .models import ItineraryTemplate, TemplateDay

    t = ItineraryTemplate.objects.create(title=title or it.title, description=f"{it.day_count} days · saved from an itinerary", created_by=user, intro=it.intro,
                                          inclusions=it.inclusions, exclusions=it.exclusions, terms=it.terms, nights_override=it.nights_override)
    for pl in it.price_lines.all():
        t.price_lines.create(description=pl.description, quantity=pl.quantity, unit_price=pl.unit_price)
    for d in it.days.select_related("destination", "accommodation"):
        nd = TemplateDay(template=t, day_number=d.day_number, title=d.title, description=d.description, destination=d.destination, accommodation=d.accommodation,
                         room_type=d.room_type, breakfast=d.breakfast, lunch=d.lunch, dinner=d.dinner, activities=d.activities, drive_km=d.drive_km,
                         drive_hours=d.drive_hours, lodging_pp=d.lodging_pp, activities_pp=d.activities_pp, transport=d.transport)
        if d.image:
            copy_day_image(d, nd)
        nd.save()
    return t


@transaction.atomic
def duplicate_template(t, user) -> "ItineraryTemplate":
    from .models import ItineraryTemplate, TemplateDay

    n = ItineraryTemplate.objects.create(title=f"{t.title} (copy)", description=t.description, created_by=user, intro=t.intro, inclusions=t.inclusions,
                                          exclusions=t.exclusions, terms=t.terms, nights_override=t.nights_override)
    for pl in t.price_lines.all():
        n.price_lines.create(description=pl.description, quantity=pl.quantity, unit_price=pl.unit_price)
    for d in t.days.all():
        nd = TemplateDay(template=n, **{f: getattr(d, f) for f in ("day_number", "title", "description", "destination", "accommodation", "room_type", "breakfast", "lunch",
                                                                     "dinner", "activities", "drive_km", "drive_hours", "lodging_pp", "activities_pp", "transport")})
        if d.image:
            copy_day_image(d, nd)
        nd.save()
    return n


@transaction.atomic
def create_itinerary(enquiry: Enquiry, user, *, package=None, title="", template=None) -> "LeadItinerary":
    from .models import LeadItinerary, LeadItineraryDay

    if template is not None:
        return itinerary_from_template(enquiry, user, template, title=title)
    pkg = package or enquiry.package
    currency = "KES" if not enquiry.client.is_international else "USD"
    it = LeadItinerary.objects.create(
        enquiry=enquiry, title=title or f"{enquiry.client.full_name} · {enquiry.interest_label}", currency=currency,
        start_date=enquiry.travel_date_from, source_package=pkg if package or (pkg and pkg.itinerary_id) else None,
        intro=(pkg.summary if pkg else ""), terms=standard_terms(), created_by=user)
    days = pkg.itinerary.days.select_related("accommodation", "destination") if pkg and pkg.itinerary_id else []
    for d in days:
        lodging = Decimal("0")
        room = ""
        if d.accommodation_id:
            amt, room = suggest_rate(d.accommodation, it.start_date + timedelta(days=d.day_number - 1) if it.start_date else None, currency)
            lodging = amt or Decimal("0")
        LeadItineraryDay.objects.create(
            itinerary=it, day_number=d.day_number, title=d.title, description=d.description, destination=d.destination,
            accommodation=d.accommodation, room_type=room, breakfast=d.breakfast, lunch=d.lunch, dinner=d.dinner,
            activities=d.activities, drive_km=d.drive_km, drive_hours=d.drive_hours, lodging_pp=lodging)
    if not days:
        LeadItineraryDay.objects.create(itinerary=it, day_number=1, title="Arrival and transfer")
    return it


@transaction.atomic
def itinerary_to_quotation(it, user) -> Quotation:
    """Draft quotation from the itinerary: lodges (per person-night), activities, transport, all with the margin applied."""
    mult = 1 + it.margin_percent / 100
    pax = Decimal(it.pax)
    items, lodges = [], {}
    for d in it.days.select_related("accommodation"):
        if d.accommodation_id and d.lodging_pp:
            key = (d.accommodation.name, d.room_type, d.lodging_pp)
            lodges[key] = lodges.get(key, 0) + 1
    for (name, room, rate), nights in lodges.items():
        items.append({"kind": "accommodation", "description": f"{name}{' · ' + room if room else ''}: {nights} night{'s' if nights != 1 else ''} per person",
                      "quantity": pax * nights, "unit_price": q2(rate * mult)})
    act = sum((d.activities_pp for d in it.days.all()), Decimal("0"))
    if act:
        items.append({"kind": "activity", "description": "Activities, park and conservancy fees per person", "quantity": pax, "unit_price": q2(act * mult)})
    transport = sum((d.transport for d in it.days.all()), Decimal("0"))
    if transport:
        items.append({"kind": "transport", "description": f"Safari vehicle, driver-guide and fuel ({it.day_count} days)", "quantity": Decimal(1),
                      "unit_price": q2(transport * mult)})
    for pl in it.price_lines.all():
        items.append({"kind": "other", "description": pl.description, "quantity": Decimal(pl.quantity), "unit_price": q2(pl.unit_price)})
    cheapest = min((h for h in it.hotels.all() if h.price_per_person), key=lambda h: h.price_per_person, default=None)
    if cheapest:
        items.append({"kind": "accommodation", "description": f"{cheapest.name} · {cheapest.meal_plan}{' · ' + cheapest.room_type if cheapest.room_type else ''} (per person)",
                      "quantity": pax, "unit_price": q2(cheapest.price_per_person)})
    ride = min((t for t in it.travel_options.all() if t.price), key=lambda t: t.total, default=None)
    if ride:
        per_person = ride.basis == ride.PERSON
        items.append({"kind": "transport", "description": f"Travel: {ride.name or ride.get_mode_display()}" + (f" · {ride.notes}" if ride.notes else "") + (" (per person)" if per_person else ""),
                      "quantity": Decimal(pax if per_person else 1), "unit_price": q2(ride.price)})
    if not items:
        raise ValueError("Price the itinerary first: add lodge rates, activities, extra lines, a lodge option or a travel option.")
    q = create_quotation(it.enquiry, user, title=it.title, currency=it.currency, items=items)
    it.quotation = q
    it.save(update_fields=["quotation", "updated_at"])
    lead_services.log_activity(it.enquiry, "note", f"Quotation {q.reference} drafted from itinerary “{it.title}”", user)
    return q


@transaction.atomic
def send_itinerary(it, via: str, user):
    if not it.days.exists():
        raise ValueError("Add at least one day first.")
    floor = SalesSettings.get().min_margin_percent
    if it.margin_percent < floor and not (user and (user.can_manage_sales or user.is_superuser)):
        raise ValueError(f"The margin ({it.margin_percent:g}%) is below the lowest allowed ({floor:g}%). Ask the sales manager to approve and send it.")
    it.status, it.sent_at = it.SENT, timezone.now()
    it.save(update_fields=["status", "sent_at", "updated_at"])
    enquiry = it.enquiry
    kind = via if via in ("whatsapp", "email") else "note"
    lead_services.log_activity(enquiry, kind, f"Itinerary “{it.title}” sent by {via} ({it.day_count} days)", user)
    if enquiry.stage in (Stage.ENQUIRY, Stage.QUOTED, Stage.HOT):
        lead_services.change_stage(enquiry, Stage.ITINERARY, user)
    return it


def enable_share(it, user=None):
    """Give the itinerary a private public-page link (kept if it already has one)."""
    import secrets

    if not it.share_token:
        it.share_token, it.shared_at, it.share_views, it.share_first_viewed_at = secrets.token_urlsafe(18), timezone.now(), 0, None
        it.save(update_fields=["share_token", "shared_at", "share_views", "share_first_viewed_at", "updated_at"])
        lead_services.log_activity(it.enquiry, "note", f"Client link created for itinerary “{it.title}”", user)
    return it


def revoke_share(it, user=None):
    if it.share_token:
        it.share_token = None
        it.save(update_fields=["share_token", "updated_at"])
        lead_services.log_activity(it.enquiry, "note", f"Client link switched off for itinerary “{it.title}”", user)
    return it


def record_view(it):
    """Count a visit to the client page; the first one tells the agent the client has opened it."""
    from django.db.models import F

    first = it.share_first_viewed_at is None
    type(it).objects.filter(pk=it.pk).update(share_views=F("share_views") + 1, share_first_viewed_at=it.share_first_viewed_at or timezone.now())
    agent = it.enquiry.assigned_agent
    if first and agent and agent.user_id:
        from apps.notifications.services import notify

        notify(agent.user, f"{it.enquiry.client.full_name} opened the itinerary", it.title, it.get_absolute_url(), "success", dedupe_key=f"itin-open:{it.pk}")


def itineraries_for(user):
    from .models import LeadItinerary

    return _scope(LeadItinerary.objects.select_related("enquiry__client", "enquiry__assigned_agent", "source_package"), user)


def record_client_response(it, kind: str, message: str = "", hotel_id=None):
    """The client answered on their itinerary link. Log it on the lead, warm the lead up, and tell the agent to call back now."""
    from .models import ClientResponse, FollowUp

    if kind not in (ClientResponse.BOOK, ClientResponse.CHANGE, ClientResponse.QUESTION):
        raise ValueError("Unknown response")
    since = timezone.now() - timedelta(hours=1)
    if it.responses.filter(created_at__gte=since).count() >= 5:
        raise ValueError("Too many messages in a short time. Please wait a little, or call us.")
    hotel = it.hotels.filter(pk=hotel_id).first() if hotel_id else None
    message = (message or "").strip()[:1000]
    r = ClientResponse.objects.create(itinerary=it, kind=kind, message=message, hotel=hotel)
    enquiry, agent = it.enquiry, it.enquiry.assigned_agent
    verb = {"book": "wants to book", "change": "asked for changes to", "question": "has a question about"}[kind]
    detail = f"{enquiry.client.full_name} {verb} itinerary “{it.title}”" + (f" (lodge: {hotel.name})" if hotel else "") + (f": {message}" if message else "")
    lead_services.log_activity(enquiry, "note", detail[:480], None)
    if kind == ClientResponse.BOOK and enquiry.stage in (Stage.ENQUIRY, Stage.QUOTED, Stage.ITINERARY):
        lead_services.change_stage(enquiry, Stage.HOT, None)
    FollowUp.objects.create(enquiry=enquiry, agent=agent, kind="call", due_at=timezone.now(),
                            title=("Client wants to book: call now" if kind == "book" else "Client replied on the itinerary: respond now"))
    if agent and agent.user_id:
        from apps.notifications.services import notify

        notify(agent.user, f"{enquiry.client.full_name} {verb} the itinerary", message[:120] or it.title, it.get_absolute_url(),
               "success" if kind == "book" else "info")
    return r


def journey(enquiry) -> list[dict]:
    """The lead's road to a booking as six steps, each done / current / todo, with the record to open when there is one."""
    from django.urls import reverse

    its = list(enquiry.itineraries.all())
    qs = list(enquiry.quotations.all())
    booking = getattr(enquiry, "booking", None)
    rank = [Stage.ENQUIRY, Stage.QUOTED, Stage.HOT, Stage.ITINERARY, Stage.DEPOSIT, Stage.CONFIRMED, Stage.COMPLETED]
    r = rank.index(enquiry.stage) if enquiry.stage in rank else 0
    proposal_sent = any(i.status == "sent" for i in its) or any(q.sent_at for q in qs) or r >= 3
    client_yes = any(x.kind == "book" for i in its for x in i.responses.all()) or r >= 2 and enquiry.stage != Stage.QUOTED
    steps = [
        ("Enquiry", True, enquiry.created_at.strftime("%d %b"), ""),
        ("Itinerary", bool(its), f"{len(its)} built" if its else "Not built yet", its[0].get_absolute_url() if its else ""),
        ("Proposal sent", proposal_sent, "Sent to the client" if proposal_sent else "Not sent yet", qs[0].get_absolute_url() if qs else ""),
        ("Client says yes", bool(client_yes), "Interested" if client_yes else "Waiting", ""),
        ("Deposit paid", r >= 4 and enquiry.stage != Stage.LOST, booking.get_status_display() if booking else "Not yet", booking.get_absolute_url() if booking else ""),
        ("Trip done", r >= 6, "Completed" if r >= 6 else ("Confirmed" if r >= 5 else "Upcoming"), ""),
    ]
    out, current_set = [], False
    for label, done, sub, url in steps:
        state = "done" if done else ("current" if not current_set else "todo")
        current_set = current_set or not done
        out.append({"label": label, "state": state, "sub": sub, "url": url})
    if enquiry.stage == Stage.LOST:
        for s in out:
            if s["state"] == "current":
                s["state"] = "lost"
    return out


@transaction.atomic
def duplicate_itinerary(it, user) -> "LeadItinerary":
    """A copy of this itinerary as a fresh draft on the same lead (days, photos, lodge options, extra lines): for offering the client a second option."""
    from .models import LeadItinerary

    n = LeadItinerary.objects.create(
        enquiry=it.enquiry, title=f"{it.title} (option B)"[:160], start_date=it.start_date, intro=it.intro, currency=it.currency, margin_percent=it.margin_percent,
        source_package=it.source_package, photo_pack=it.photo_pack, nights_override=it.nights_override, inclusions=it.inclusions, exclusions=it.exclusions,
        terms=it.terms, created_by=user)
    for d in it.days.all():
        nd = type(d).objects.get(pk=d.pk)
        photos = list(d.photos.all())
        nd.pk, nd.itinerary = None, n
        nd.image = ""
        nd.save()
        nd.photos.set(photos)
        if d.image:
            copy_day_image(d, nd)
            nd.save()
    for pl in it.price_lines.all():
        n.price_lines.create(description=pl.description, quantity=pl.quantity, unit_price=pl.unit_price)
    for h in it.hotels.all():
        n.hotels.create(photo_pack=h.photo_pack, name=h.name, meal_plan=h.meal_plan, room_type=h.room_type, price_per_person=h.price_per_person)
    for t in it.travel_options.all():
        n.travel_options.create(mode=t.mode, name=t.name, basis=t.basis, price=t.price, notes=t.notes)
    lead_services.log_activity(it.enquiry, "note", f"Itinerary “{it.title}” copied to “{n.title}”", user)
    return n


def enable_portal(booking, user=None):
    """Give the booking a private trip page for the client (kept if it already has one)."""
    import secrets

    if not booking.portal_token:
        booking.portal_token = secrets.token_urlsafe(18)
        booking.save(update_fields=["portal_token", "updated_at"])
        lead_services.log_activity(booking.enquiry, "note", f"Client trip page created for {booking.reference}", user)
    return booking


def revoke_portal(booking, user=None):
    if booking.portal_token:
        booking.portal_token = None
        booking.save(update_fields=["portal_token", "updated_at"])
        lead_services.log_activity(booking.enquiry, "note", f"Client trip page switched off for {booking.reference}", user)
    return booking


def submit_feedback(booking, *, rating, nps, guide_rating=None, lodge_rating=None, comment="", may_quote=False):
    """Save the client's rating. A low score alerts the managers and asks the agent to call; a high one asks the agent to request a review."""
    from apps.notifications.services import notify, notify_roles

    from .models import FollowUp, TripFeedback

    def _n(v, lo, hi):
        v = int(v)
        if not lo <= v <= hi:
            raise ValueError("Scores must be within range")
        return v

    if TripFeedback.objects.filter(booking=booking).exists():
        raise ValueError("Thank you, we already have your feedback.")
    fb = TripFeedback.objects.create(
        booking=booking, rating=_n(rating, 1, 5), nps=_n(nps, 0, 10), guide_rating=_n(guide_rating, 1, 5) if guide_rating not in (None, "") else None,
        lodge_rating=_n(lodge_rating, 1, 5) if lodge_rating not in (None, "") else None, comment=(comment or "").strip()[:1500], may_quote=bool(may_quote))
    name, agent = booking.client.full_name, booking.agent
    if fb.rating <= 3 or fb.nps <= 6:
        notify_roles(["Sales Manager", "COO", "Admin"], f"Unhappy client: {name}", f"{booking.reference}: {fb.rating}/5, would recommend {fb.nps}/10. {fb.comment[:100]}",
                     booking.get_absolute_url(), "danger", dedupe_key=f"fb-low:{booking.pk}")
        FollowUp.objects.create(enquiry=booking.enquiry, agent=agent, kind="call", due_at=timezone.now(), title=f"Call {name}: low trip rating, put it right")
    elif fb.nps >= 9 and fb.rating >= 4:
        FollowUp.objects.create(enquiry=booking.enquiry, agent=agent, kind="whatsapp", due_at=timezone.now() + timedelta(days=1), title=f"Thank {name} and ask for a review")
    if agent and agent.user_id:
        notify(agent.user, f"{name} rated their trip {fb.rating}/5", fb.comment[:120], booking.get_absolute_url(), "danger" if fb.rating <= 3 else "success")
    lead_services.log_activity(booking.enquiry, "note", f"Trip feedback: {fb.rating}/5, would recommend {fb.nps}/10" + (f". “{fb.comment[:200]}”" if fb.comment else ""), None)
    return fb
