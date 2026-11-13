from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils import timezone

from apps.core.models import Sequence, TimeStampedModel

CURRENCIES = [("KES", "KES"), ("USD", "USD"), ("EUR", "EUR"), ("GBP", "GBP")]
CENT = Decimal("0.01")


def q2(value) -> Decimal:
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------------------------------------- settings & rules
class SalesSettings(models.Model):
    """One row of tunables for the sales team (first-response SLA, auto-assignment)."""

    response_sla_minutes = models.PositiveIntegerField(
        default=30, help_text="An enquiry should get a first call/WhatsApp/e-mail within this many minutes.")
    auto_assign_new = models.BooleanField(
        "Auto-assign new enquiries", default=False,
        help_text="New enquiries are given to an agent by the rules below, then round-robin.")
    quote_valid_days = models.PositiveSmallIntegerField(default=14)
    vat_rate = models.DecimalField("VAT rate %", max_digits=5, decimal_places=2, default=Decimal("16.00"))
    follow_up_stale_days = models.PositiveSmallIntegerField(
        default=5, help_text="Open leads with no contact for this many days are flagged as going cold.")
    route_by_specialty = models.BooleanField(
        "Route by specialist", default=True,
        help_text="After the rules, prefer agents whose specialties match the destination or interest (e.g. Mara, honeymoon, cultural), then round-robin among them.")
    min_margin_percent = models.DecimalField("Lowest allowed margin %", max_digits=5, decimal_places=2, default=Decimal("10"),
                                             help_text="An itinerary priced below this margin can only be sent to a client by a sales manager.")
    automation_on = models.BooleanField("Sales automation", default=True, help_text="Master switch for the automatic follow-ups and reminders below.")
    auto_quote_chase_days = models.PositiveSmallIntegerField("Chase a quotation after (days)", default=2, help_text="0 turns it off.")
    auto_reconnect_days = models.PositiveSmallIntegerField("Reconnect with a quiet lead after (days)", default=5, help_text="0 turns it off.")
    auto_deposit_chase_days = models.PositiveSmallIntegerField("Chase an unpaid deposit after (days)", default=3, help_text="0 turns it off.")
    auto_balance_chase_days = models.PositiveSmallIntegerField("Chase the balance this many days before travel", default=30, help_text="0 turns it off.")
    auto_pretrip_days = models.PositiveSmallIntegerField("Pre-trip check this many days before travel", default=7, help_text="Confirms documents, and makes sure the client's trip page exists. 0 turns it off.")
    auto_feedback_after_days = models.PositiveSmallIntegerField("Ask for trip feedback this many days after return", default=1, help_text="0 turns it off.")
    auto_callback_after_call = models.BooleanField("Schedule the next step after a call", default=True,
                                                   help_text="Interested: send the quotation tomorrow. Voicemail: call back tomorrow.")
    auto_plan_reminders = models.BooleanField("Remind people to plan their week and mark their day", default=True)
    require_kyc_for_deposit = models.BooleanField(
        "Require KYC before a deposit", default=False, help_text="When on, the first payment on a booking is refused until the client's identity is verified.")

    class Meta:
        verbose_name_plural = "sales settings"

    def __str__(self):
        return "Sales settings"

    @classmethod
    def get(cls) -> "SalesSettings":
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class AssignmentRule(models.Model):
    """First matching active rule (lowest priority number) whose agent is available wins; else round-robin."""

    name = models.CharField(max_length=80)
    priority = models.PositiveSmallIntegerField(default=10, help_text="Lower numbers are checked first.")
    source = models.CharField(max_length=12, blank=True, help_text="Leave empty for any channel.")
    purpose = models.CharField(max_length=12, blank=True, help_text="Leave empty for any purpose.")
    destination = models.ForeignKey("catalog.Destination", null=True, blank=True, on_delete=models.CASCADE,
                                    related_name="+", help_text="Leave empty for any destination.")
    international_only = models.BooleanField(default=False, help_text="Only diaspora / international clients.")
    agent = models.ForeignKey("people.Employee", on_delete=models.CASCADE, related_name="assignment_rules")
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["priority", "pk"]

    def __str__(self):
        return self.name

    def matches(self, enquiry) -> bool:
        if self.source and enquiry.source != self.source:
            return False
        if self.purpose and enquiry.purpose != self.purpose:
            return False
        if self.destination_id and enquiry.destination_id != self.destination_id:
            return False
        if self.international_only and not enquiry.client.is_international:
            return False
        return True

    @property
    def criteria(self) -> str:
        from apps.leads.models import Purpose, Source

        bits = []
        if self.source:
            bits.append(__import__('apps.leads.models', fromlist=['x']).source_labels().get(self.source, self.source))
        if self.purpose:
            bits.append(dict(Purpose.choices).get(self.purpose, self.purpose))
        if self.destination_id:
            bits.append(self.destination.name)
        if self.international_only:
            bits.append("International clients")
        return " · ".join(bits) or "Everything"


class SalesTarget(models.Model):
    """Monthly goal per agent. Revenue is in KES."""

    agent = models.ForeignKey("people.Employee", on_delete=models.CASCADE, related_name="sales_targets")
    year = models.PositiveSmallIntegerField()
    month = models.PositiveSmallIntegerField()
    revenue_target = models.DecimalField("Revenue target (KES)", max_digits=14, decimal_places=2, default=0)
    bookings_target = models.PositiveSmallIntegerField(default=0)
    calls_target = models.PositiveSmallIntegerField(default=0, help_text="Optional: calls to log in the month.")

    class Meta:
        unique_together = [("agent", "year", "month")]
        ordering = ["-year", "-month", "agent__full_name"]

    def __str__(self):
        return f"{self.agent} · {self.year}-{self.month:02d}"


# ---------------------------------------------------------------------------------------------- calls & tasks
class CallLog(models.Model):
    DIRECTIONS = [("out", "Outbound"), ("in", "Inbound")]
    OUTCOMES = [("connected", "Spoke to client"), ("no_answer", "No answer"), ("voicemail", "Left voicemail"),
                ("busy", "Busy / call back later"), ("wrong_number", "Wrong number"),
                ("interested", "Interested — send quote"), ("not_interested", "Not interested")]
    CONNECTED = {"connected", "interested", "not_interested"}

    enquiry = models.ForeignKey("leads.Enquiry", on_delete=models.CASCADE, related_name="calls")
    agent = models.ForeignKey("people.Employee", null=True, blank=True, on_delete=models.SET_NULL,
                              related_name="calls")
    logged_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                  related_name="+")
    direction = models.CharField(max_length=3, choices=DIRECTIONS, default="out")
    outcome = models.CharField(max_length=14, choices=OUTCOMES, default="connected")
    duration_seconds = models.PositiveIntegerField(default=0)
    notes = models.TextField(blank=True)
    called_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-called_at", "-pk"]

    def __str__(self):
        return f"{self.get_outcome_display()} · {self.enquiry.reference}"

    @property
    def connected(self) -> bool:
        return self.outcome in self.CONNECTED


class FollowUp(TimeStampedModel):
    KINDS = [("call", "Call"), ("whatsapp", "WhatsApp"), ("email", "E-mail"), ("send_quote", "Send quotation"),
             ("chase_payment", "Chase payment"), ("meeting", "Meeting"), ("other", "Other")]

    enquiry = models.ForeignKey("leads.Enquiry", on_delete=models.CASCADE, related_name="followups")
    agent = models.ForeignKey("people.Employee", null=True, blank=True, on_delete=models.SET_NULL,
                              related_name="followups")
    kind = models.CharField(max_length=14, choices=KINDS, default="call")
    title = models.CharField(max_length=160)
    due_at = models.DateTimeField(db_index=True)
    done_at = models.DateTimeField(null=True, blank=True, db_index=True)
    auto_key = models.CharField(max_length=60, blank=True, db_index=True, help_text="Set when the system created this task itself; stops it being created twice.")

    class Meta:
        ordering = ["done_at", "due_at"]

    @property
    def is_auto(self) -> bool:
        return bool(self.auto_key)

    def __str__(self):
        return self.title

    @property
    def is_done(self) -> bool:
        return self.done_at is not None

    @property
    def is_overdue(self) -> bool:
        return not self.done_at and self.due_at < timezone.now()

    @property
    def is_today(self) -> bool:
        return not self.done_at and timezone.localtime(self.due_at).date() == timezone.localdate()


# ---------------------------------------------------------------------------------------------- quotations
class QuotationStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    SENT = "sent", "Sent"
    ACCEPTED = "accepted", "Accepted"
    REJECTED = "rejected", "Rejected"
    EXPIRED = "expired", "Expired"
    SUPERSEDED = "superseded", "Superseded"


class Quotation(TimeStampedModel):
    """One version of a price offer. Revising creates version n+1 and supersedes the old one."""

    reference = models.CharField(max_length=20, blank=True, db_index=True)
    enquiry = models.ForeignKey("leads.Enquiry", on_delete=models.CASCADE, related_name="quotations")
    version = models.PositiveSmallIntegerField(default=1)
    agent = models.ForeignKey("people.Employee", null=True, blank=True, on_delete=models.SET_NULL,
                              related_name="quotations")
    title = models.CharField(max_length=160)
    intro = models.TextField(blank=True)
    currency = models.CharField(max_length=3, choices=CURRENCIES, default="USD")
    fx_rate = models.DecimalField("Rate to KES", max_digits=14, decimal_places=6, default=1)
    discount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    vat_rate = models.DecimalField("VAT %", max_digits=5, decimal_places=2, default=0,
                                   help_text="0 for exempt / export services.")
    valid_until = models.DateField(null=True, blank=True)
    terms = models.TextField(blank=True)
    status = models.CharField(max_length=12, choices=QuotationStatus.choices, default=QuotationStatus.DRAFT,
                              db_index=True)
    subtotal = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    vat_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    sent_at = models.DateTimeField(null=True, blank=True)
    sent_via = models.CharField(max_length=10, blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["-created_at"]
        unique_together = [("enquiry", "version")]

    def __str__(self):
        return f"{self.reference} v{self.version}"

    def save(self, *args, **kwargs):
        if not self.reference:
            self.reference = Sequence.next_number("quotation", "QT", 4)
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse("sales:quotation_detail", args=[self.pk])

    @property
    def is_editable(self) -> bool:
        return self.status == QuotationStatus.DRAFT

    @property
    def is_live(self) -> bool:
        return self.status in (QuotationStatus.DRAFT, QuotationStatus.SENT)

    @property
    def is_expired(self) -> bool:
        return bool(self.valid_until and self.valid_until < timezone.localdate()
                    and self.status in (QuotationStatus.SENT,))

    @property
    def total_kes(self) -> Decimal:
        return q2(self.total * self.fx_rate)

    def recalculate(self, save=True):
        sub = sum((i.line_total for i in self.items.all()), Decimal("0"))
        taxable = max(sub - self.discount, Decimal("0"))
        self.subtotal = q2(sub)
        self.vat_amount = q2(taxable * self.vat_rate / 100)
        self.total = q2(taxable + self.vat_amount)
        if save:
            self.save(update_fields=["subtotal", "vat_amount", "total", "updated_at"])
        return self.total


class QuotationItem(models.Model):
    KINDS = [("accommodation", "Accommodation"), ("transport", "Transport & vehicle"),
             ("park_fees", "Park & conservancy fees"), ("activity", "Activities"), ("flight", "Flights"),
             ("guide", "Guide / driver"), ("meals", "Meals"), ("other", "Other")]

    quotation = models.ForeignKey(Quotation, on_delete=models.CASCADE, related_name="items")
    kind = models.CharField(max_length=14, choices=KINDS, default="accommodation")
    description = models.CharField(max_length=200)
    quantity = models.DecimalField(max_digits=8, decimal_places=2, default=1)
    unit_price = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["position", "pk"]

    def __str__(self):
        return self.description

    @property
    def line_total(self) -> Decimal:
        return q2(self.quantity * self.unit_price)


# ---------------------------------------------------------------------------------------------- bookings
class BookingStatus(models.TextChoices):
    PENDING = "pending", "Awaiting deposit"
    DEPOSIT = "deposit", "Deposit paid"
    CONFIRMED = "confirmed", "Confirmed"
    COMPLETED = "completed", "Trip completed"
    CANCELLED = "cancelled", "Cancelled"


class Booking(TimeStampedModel):
    reference = models.CharField(max_length=20, unique=True, blank=True)
    enquiry = models.OneToOneField("leads.Enquiry", on_delete=models.PROTECT, related_name="booking")
    quotation = models.ForeignKey(Quotation, null=True, blank=True, on_delete=models.SET_NULL, related_name="bookings")
    client = models.ForeignKey("contacts.Client", on_delete=models.PROTECT, related_name="bookings")
    agent = models.ForeignKey("people.Employee", null=True, blank=True, on_delete=models.SET_NULL,
                              related_name="bookings")
    package = models.ForeignKey("catalog.TourPackage", null=True, blank=True, on_delete=models.SET_NULL,
                                related_name="bookings")
    departure = models.ForeignKey("trips.Departure", null=True, blank=True, on_delete=models.SET_NULL,
                                  related_name="bookings")
    title = models.CharField(max_length=160)
    travel_start = models.DateField(null=True, blank=True)
    travel_end = models.DateField(null=True, blank=True)
    adults = models.PositiveSmallIntegerField(default=2)
    children = models.PositiveSmallIntegerField(default=0)
    currency = models.CharField(max_length=3, choices=CURRENCIES, default="USD")
    fx_rate = models.DecimalField("Rate to KES", max_digits=14, decimal_places=6, default=1)
    total_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    deposit_percent = models.PositiveSmallIntegerField(default=30)
    status = models.CharField(max_length=10, choices=BookingStatus.choices, default=BookingStatus.PENDING,
                              db_index=True)
    booked_at = models.DateTimeField(default=timezone.now, db_index=True)
    cancelled_reason = models.CharField(max_length=200, blank=True)
    notes = models.TextField(blank=True)
    portal_token = models.CharField(max_length=40, unique=True, null=True, blank=True, editable=False)
    portal_views = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-booked_at"]

    def __str__(self):
        return f"{self.reference} · {self.client}"

    def save(self, *args, **kwargs):
        if not self.reference:
            self.reference = Sequence.next_number("booking", "BK", 5)
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse("sales:booking_detail", args=[self.pk])

    @property
    def deposit_amount(self) -> Decimal:
        return q2(self.total_amount * self.deposit_percent / 100)

    @property
    def amount_paid(self) -> Decimal:
        if not hasattr(self, "_paid"):
            self._paid = sum((p.amount for p in self.payments.all()), Decimal("0"))
        return self._paid

    @property
    def balance(self) -> Decimal:
        return max(q2(self.total_amount - self.amount_paid), Decimal("0"))

    @property
    def total_kes(self) -> Decimal:
        return q2(self.total_amount * self.fx_rate)

    @property
    def paid_percent(self) -> int:
        return min(100, round(100 * self.amount_paid / self.total_amount)) if self.total_amount else 0

    @property
    def payment_state(self) -> str:
        if self.amount_paid <= 0:
            return "unpaid"
        if self.balance <= 0:
            return "paid"
        return "deposit" if self.amount_paid >= self.deposit_amount else "partial"

    @property
    def party_label(self):
        s = f"{self.adults} adult{'s' if self.adults != 1 else ''}"
        return s + (f", {self.children} child{'ren' if self.children != 1 else ''}" if self.children else "")

    @property
    def trip_name(self):
        """The title without the party size that was appended when the booking was made (it is shown on its own line)."""
        import re

        return re.sub(r"\s·\s\d+ adults?(, \d+ child(ren)?)?\s*$", "", self.title or "")


class BookingPayment(models.Model):
    METHODS = [("mpesa", "M-PESA"), ("bank", "Bank transfer"), ("card", "Card"), ("cash", "Cash"), ("other", "Other")]
    booking = models.ForeignKey(Booking, on_delete=models.CASCADE, related_name="payments")
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    method = models.CharField(max_length=6, choices=METHODS, default="mpesa")
    reference = models.CharField("Transaction code", max_length=60, blank=True)
    received_at = models.DateTimeField(default=timezone.now)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+")
    note = models.CharField(max_length=160, blank=True)

    class Meta:
        ordering = ["-received_at", "-pk"]

    def __str__(self):
        return f"{self.booking.reference} · {self.amount}"


class DoubleAllocation(models.Model):
    """The same guest is being worked by more than one consultant at once."""

    OPEN, RESOLVED = "open", "resolved"
    client = models.ForeignKey("contacts.Client", on_delete=models.CASCADE, related_name="double_allocations")
    status = models.CharField(max_length=8, choices=[(OPEN, "Open"), (RESOLVED, "Resolved")], default=OPEN,
                              db_index=True)
    detected_by = models.CharField(max_length=10, default="phone", help_text="phone / email / name")
    detected_at = models.DateTimeField(default=timezone.now)
    kept_agent = models.ForeignKey("people.Employee", null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")
    reason = models.CharField(max_length=200, blank=True)
    resolved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+")
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["status", "-detected_at"]

    def __str__(self):
        return f"{self.client} · {self.get_status_display()}"


# ---------------------------------------------------------------------------------------------- itinerary builder
class LeadItinerary(TimeStampedModel):
    """A custom day-by-day itinerary an agent builds for one lead, priced from lodge rates, then turned into a quotation."""

    DRAFT, SENT = "draft", "sent"
    enquiry = models.ForeignKey("leads.Enquiry", on_delete=models.CASCADE, related_name="itineraries")
    title = models.CharField(max_length=160)
    start_date = models.DateField(null=True, blank=True)
    intro = models.TextField(blank=True, help_text="Shown at the top of the itinerary the client receives")
    currency = models.CharField(max_length=3, choices=CURRENCIES, default="USD")
    margin_percent = models.DecimalField("Margin %", max_digits=5, decimal_places=2, default=Decimal("20"))
    status = models.CharField(max_length=6, choices=[(DRAFT, "Draft"), (SENT, "Sent")], default=DRAFT, db_index=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    source_package = models.ForeignKey("catalog.TourPackage", null=True, blank=True, on_delete=models.SET_NULL,
                                       related_name="+")
    quotation = models.ForeignKey(Quotation, null=True, blank=True, on_delete=models.SET_NULL, related_name="itineraries")
    share_token = models.CharField(max_length=40, unique=True, null=True, blank=True, editable=False)
    shared_at = models.DateTimeField(null=True, blank=True)
    share_views = models.PositiveIntegerField(default=0)
    share_first_viewed_at = models.DateTimeField(null=True, blank=True)
    photo_pack = models.ForeignKey("catalog.PhotoCategory", null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
                                   verbose_name="Photo pack", help_text="Photos for the cover and for days without their own picture")
    nights_override = models.PositiveSmallIntegerField("Nights", null=True, blank=True, help_text="Leave empty to work it out from the days")
    inclusions = models.TextField(blank=True, help_text="One item per line")
    exclusions = models.TextField(blank=True, help_text="One item per line")
    terms = models.TextField("Payment & cancellation terms", blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse("sales:itinerary_detail", args=[self.pk])

    @property
    def nights(self) -> int:
        return self.nights_override if self.nights_override is not None else max(self.day_count - 1, 0)

    @property
    def effective_terms(self) -> str:
        """The itinerary's own terms, or the standard ones Operations has set when it has none."""
        from .services import standard_terms

        return (self.terms or "").strip() or standard_terms()

    @property
    def inclusion_list(self):
        return [l.strip() for l in self.inclusions.splitlines() if l.strip()]

    @property
    def exclusion_list(self):
        return [l.strip() for l in self.exclusions.splitlines() if l.strip()]

    @property
    def lines_total(self) -> Decimal:
        """Extra priced lines (park fees, flights, visas...) added after the margin."""
        return q2(sum((p.line_total for p in self.price_lines.all()), Decimal("0")))

    @property
    def hotel_from(self) -> Decimal:
        """Hotel options are alternatives, so the headline price uses the cheapest priced one."""
        totals = [h.price_per_person * self.pax for h in self.hotels.all() if h.price_per_person]
        return q2(min(totals)) if totals else Decimal("0")

    @property
    def travel_from(self) -> Decimal:
        """Travel options are alternatives too, so the headline price uses the cheapest priced one."""
        totals = [t.total for t in self.travel_options.all() if t.price]
        return q2(min(totals)) if totals else Decimal("0")

    def public_path(self) -> str:
        return reverse("public_trip", args=[self.share_token]) if self.share_token else ""

    @property
    def proposal_path(self) -> str:
        """The designed proposal for the client: opens without a login from the private link."""
        return reverse("public_trip_proposal", args=[self.share_token]) if self.share_token else ""

    @property
    def pdf_path(self) -> str:
        return reverse("public_trip_pdf", args=[self.share_token]) if self.share_token else ""

    @property
    def day_count(self) -> int:
        return len(self.days.all())

    @property
    def end_date(self):
        return self.start_date + timedelta(days=self.day_count - 1) if self.start_date and self.day_count else None

    @property
    def pax(self) -> int:
        return max(self.enquiry.adults + self.enquiry.children, 1)

    @property
    def cost_total(self) -> Decimal:
        pax = self.pax
        return q2(sum((d.lodging_pp * pax + d.activities_pp * pax + d.transport for d in self.days.all()), Decimal("0")))

    @property
    def day_sell(self) -> Decimal:
        """What the days (lodging, activities, vehicle) sell for, after the margin."""
        return q2(self.cost_total * (1 + self.margin_percent / 100))

    @property
    def sell_total(self) -> Decimal:
        """The price the client sees: days after margin, plus extra lines, plus the cheapest lodge option."""
        return q2(self.day_sell + self.lines_total + self.hotel_from + self.travel_from)

    @property
    def grand_total(self) -> Decimal:
        return self.sell_total

    @property
    def sell_pp(self) -> Decimal:
        return q2(self.sell_total / self.pax)

    @property
    def profit(self) -> Decimal:
        return q2(self.day_sell - self.cost_total)


class LeadItineraryDay(models.Model):
    itinerary = models.ForeignKey(LeadItinerary, on_delete=models.CASCADE, related_name="days")
    day_number = models.PositiveSmallIntegerField(default=1)
    title = models.CharField(max_length=160)
    description = models.TextField(blank=True)
    destination = models.ForeignKey("catalog.Destination", null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+")
    accommodation = models.ForeignKey("catalog.Accommodation", null=True, blank=True, on_delete=models.SET_NULL,
                                      related_name="+")
    room_type = models.CharField(max_length=60, blank=True)
    breakfast = models.BooleanField(default=False)
    lunch = models.BooleanField(default=False)
    dinner = models.BooleanField(default=False)
    activities = models.CharField(max_length=300, blank=True, help_text="Comma separated")
    drive_km = models.PositiveIntegerField(null=True, blank=True)
    drive_hours = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)
    lodging_pp = models.DecimalField("Lodging / person (night)", max_digits=14, decimal_places=2, default=0)
    activities_pp = models.DecimalField("Activities & fees / person", max_digits=14, decimal_places=2, default=0)
    transport = models.DecimalField("Vehicle & guide (whole group)", max_digits=14, decimal_places=2, default=0)
    image = models.ImageField("Picture for this day", upload_to="itinerary/days/%Y/%m/", blank=True,
                              help_text="Shown with this day on the itinerary the client receives. JPG or PNG, up to 5 MB.")
    photos = models.ManyToManyField("catalog.LibraryPhoto", blank=True, related_name="used_on_days")

    class Meta:
        ordering = ["day_number", "pk"]

    def __str__(self):
        return f"Day {self.day_number}: {self.title}"

    @property
    def gallery_files(self):
        """Up to two picked library photos (file fields), else the single picture_file."""
        files = [p.image for p in self.photos.all()[:2]]
        if self.image:
            files = [self.image] + files[:1]
        if not files and self.picture_file:
            files = [self.picture_file]
        return files

    @property
    def picture_file(self):
        """The day's own upload, else the first photo picked from the library, else the first gallery photo of its destination (a file field), else None."""
        if self.image:
            return self.image
        picked = self.photos.first() if self.pk and hasattr(self, "photos") else None
        if picked:
            return picked.image
        if self.destination_id:                 # the destination's tagged photos, then its older admin gallery
            photo = self.destination.pack_photos.first() or self.destination.photos.first()
            return photo.image if photo else None
        return None

    @property
    def picture_url(self) -> str:
        f = self.picture_file
        return f.url if f else ""

    @property
    def meals_code(self) -> str:
        return "".join(c for c, f in (("B", self.breakfast), ("L", self.lunch), ("D", self.dinner)) if f)

    @property
    def activity_list(self):
        return [a.strip() for a in self.activities.split(",") if a.strip()]

    @property
    def story(self) -> str:
        """The description without the parts the page already shows: a first sentence that just repeats the day's title,
        and a closing "Activities: ..." sentence (the activities are shown as tags)."""
        import re

        text = (self.description or "").strip()
        title = (self.title or "").strip().rstrip(".")
        if title and text.lower().startswith(title.lower()):
            text = text[len(title):].lstrip(" .:-–—\n")
        if self.activity_list:
            text = re.sub(r"(^|\s)Activities?:[^.\n]*\.?\s*$", "", text, flags=re.I)
        return text.strip()

    @property
    def date(self):
        it = self.itinerary
        return it.start_date + timedelta(days=self.day_number - 1) if it.start_date else None


LeadItinerary.days_label = property(lambda self: f"{self.day_count} days")


MEAL_PLANS = [(m, m) for m in ("Bed & Breakfast", "Half Board", "Full Board", "All Inclusive")]


class ClientResponse(TimeStampedModel):
    """What the client did on their itinerary link: wants to book, wants a change, or asked a question."""

    BOOK, CHANGE, QUESTION = "book", "change", "question"
    KINDS = [(BOOK, "Wants to book"), (CHANGE, "Asked for a change"), (QUESTION, "Asked a question")]
    itinerary = models.ForeignKey(LeadItinerary, on_delete=models.CASCADE, related_name="responses")
    kind = models.CharField(max_length=10, choices=KINDS, default=BOOK)
    message = models.TextField(blank=True)
    hotel = models.ForeignKey("ItineraryHotel", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    handled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_kind_display()} · {self.itinerary}"


class ItineraryPriceLine(models.Model):
    """An extra priced line on an itinerary (park fees, flights, visas...), added to the sell price without margin."""

    itinerary = models.ForeignKey(LeadItinerary, on_delete=models.CASCADE, related_name="price_lines")
    description = models.CharField(max_length=200)
    quantity = models.PositiveIntegerField(default=1)
    unit_price = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    class Meta:
        ordering = ["pk"]

    def __str__(self):
        return self.description

    @property
    def line_total(self) -> Decimal:
        return self.unit_price * self.quantity


class ItineraryHotel(models.Model):
    """A lodge option quoted on an itinerary with its own per-person price; options are alternatives for the client to choose from."""

    itinerary = models.ForeignKey(LeadItinerary, on_delete=models.CASCADE, related_name="hotels")
    accommodation = models.ForeignKey("catalog.Accommodation", null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
                                      help_text="From the Accommodations module; its photos are used for this option.")
    photo_pack = models.ForeignKey("catalog.PhotoCategory", null=True, blank=True, on_delete=models.SET_NULL, related_name="+", verbose_name="Accommodation photos",
                                   limit_choices_to={"kind": "accommodation"})
    name = models.CharField(max_length=150, blank=True)
    meal_plan = models.CharField(max_length=30, choices=MEAL_PLANS, default="Full Board")
    room_type = models.CharField(max_length=120, blank=True)
    price_per_person = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    class Meta:
        ordering = ["pk"]

    def __str__(self):
        return self.name or (self.accommodation.name if self.accommodation_id else self.photo_pack.name if self.photo_pack_id else "Accommodation option")

    @property
    def total(self) -> Decimal:
        return q2(self.price_per_person * self.itinerary.pax)

    @property
    def pack(self):
        """The photos to show: the accommodation's own (always current), else a pack chosen before options came from Accommodations."""
        if self.accommodation_id and self.accommodation.photo_pack_id:
            return self.accommodation.photo_pack
        return self.photo_pack if self.photo_pack_id else None



class ItineraryTravel(models.Model):
    """A way of getting there quoted on an itinerary (a safari van, a Noah, a Land Cruiser, the SGR, a flight) with its own price.
    Like lodge options these are alternatives for the client to choose from; the cheapest sets the headline price."""

    VEHICLE, NOAH, COASTER, CRUISER, TRAIN, FLIGHT, BUS, OTHER = "vehicle", "noah", "coaster", "cruiser", "train", "flight", "bus", "other"
    MODES = [(VEHICLE, "Safari vehicle"), (NOAH, "Noah / van"), (COASTER, "Coaster van"), (CRUISER, "Land Cruiser"), (TRAIN, "SGR train"), (FLIGHT, "Flight"), (BUS, "Coach / bus"), (OTHER, "Other")]
    PERSON, TOTAL = "person", "total"
    BASES = [(PERSON, "Per person"), (TOTAL, "For the whole group")]

    itinerary = models.ForeignKey(LeadItinerary, on_delete=models.CASCADE, related_name="travel_options")
    mode = models.CharField(max_length=10, choices=MODES, default=VEHICLE)
    name = models.CharField("Name", max_length=150, blank=True, help_text="e.g. Noah with pop-up roof, SGR economy + safari van")
    basis = models.CharField("Priced", max_length=6, choices=BASES, default=TOTAL)
    price = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    notes = models.CharField(max_length=200, blank=True, help_text="e.g. 7 seats, driver-guide and fuel included")

    class Meta:
        ordering = ["pk"]

    def __str__(self):
        return self.name or self.get_mode_display()

    @property
    def total(self) -> Decimal:
        """What this option costs the whole group."""
        return q2(self.price * self.itinerary.pax if self.basis == self.PERSON else self.price)

    @property
    def per_person(self) -> Decimal:
        return q2(self.total / self.itinerary.pax)


class ItineraryTemplate(TimeStampedModel):
    """A reusable day-by-day itinerary the whole sales team can start from, or borrow single days from."""

    title = models.CharField(max_length=160)
    description = models.CharField(max_length=300, blank=True, help_text="What this template is for, e.g. 'Honeymoon, 5 days Mara + Diani'")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    times_used = models.PositiveIntegerField(default=0)
    intro = models.TextField(blank=True)
    inclusions = models.TextField(blank=True, help_text="One item per line")
    exclusions = models.TextField(blank=True, help_text="One item per line")
    terms = models.TextField(blank=True)
    nights_override = models.PositiveSmallIntegerField(null=True, blank=True)

    class Meta:
        ordering = ["title"]

    def __str__(self):
        return self.title

    @property
    def day_count(self) -> int:
        return len(self.days.all())

    @property
    def places(self) -> str:
        seen = []
        for d in self.days.all():
            if d.destination_id and d.destination.name not in seen:
                seen.append(d.destination.name)
        return " · ".join(seen)

    def get_absolute_url(self):
        return reverse("sales:template_detail", args=[self.pk])

    @property
    def lodging_pp_total(self) -> Decimal:
        return sum((d.lodging_pp for d in self.days.all()), Decimal("0"))

    @property
    def cost_pp(self) -> Decimal:
        """Rough per-person cost: lodging plus activities (vehicle and guide are per group, so left out)."""
        return sum((d.lodging_pp + d.activities_pp for d in self.days.all()), Decimal("0"))

    @property
    def stays(self) -> list:
        seen = {}
        for d in self.days.all():
            if d.accommodation_id:
                seen[d.accommodation.name] = seen.get(d.accommodation.name, 0) + 1
        return [{"name": k, "nights": v} for k, v in seen.items()]

    @property
    def activity_names(self) -> list:
        out = []
        for d in self.days.all():
            for a in d.activity_list:
                if a not in out:
                    out.append(a)
        return out

    @property
    def cover_url(self) -> str:
        for d in self.days.all():
            f = d.picture_file
            if f:
                return f.url
        return ""


class TemplateDay(models.Model):
    template = models.ForeignKey(ItineraryTemplate, on_delete=models.CASCADE, related_name="days")
    day_number = models.PositiveSmallIntegerField(default=1)
    title = models.CharField(max_length=160)
    description = models.TextField(blank=True)
    destination = models.ForeignKey("catalog.Destination", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    accommodation = models.ForeignKey("catalog.Accommodation", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    room_type = models.CharField(max_length=60, blank=True)
    breakfast = models.BooleanField(default=False)
    lunch = models.BooleanField(default=False)
    dinner = models.BooleanField(default=False)
    activities = models.CharField(max_length=300, blank=True, help_text="Comma separated")
    drive_km = models.PositiveIntegerField(null=True, blank=True)
    drive_hours = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)
    lodging_pp = models.DecimalField("Lodging / person (night)", max_digits=14, decimal_places=2, default=0)
    activities_pp = models.DecimalField("Activities & fees / person", max_digits=14, decimal_places=2, default=0)
    transport = models.DecimalField("Vehicle & guide (whole group)", max_digits=14, decimal_places=2, default=0)
    image = models.ImageField("Picture for this day", upload_to="itinerary/templates/%Y/%m/", blank=True,
                              help_text="Shown with this day on the itinerary the client receives. JPG or PNG, up to 5 MB.")

    class Meta:
        ordering = ["day_number", "pk"]

    def __str__(self):
        return f"Day {self.day_number}: {self.title}"

    picture_file = LeadItineraryDay.picture_file
    picture_url = LeadItineraryDay.picture_url
    meals_code = LeadItineraryDay.meals_code
    activity_list = LeadItineraryDay.activity_list

    @property
    def payload(self) -> dict:
        """What the builder needs to drop this day into another itinerary or template."""
        return {"id": self.pk, "template": self.template.title, "label": f"Day {self.day_number} · {self.title}", "title": self.title, "description": self.description,
                "destination": self.destination_id or "", "accommodation": self.accommodation_id or "", "room_type": self.room_type, "breakfast": self.breakfast,
                "lunch": self.lunch, "dinner": self.dinner, "activities": self.activities, "drive_km": self.drive_km or "", "drive_hours": str(self.drive_hours or ""),
                "lodging_pp": str(self.lodging_pp), "activities_pp": str(self.activities_pp), "transport": str(self.transport),
                "image_url": self.image.url if self.image else ""}

    @property
    def payload_json(self) -> str:
        import json

        return json.dumps(self.payload)


class TemplatePriceLine(models.Model):
    template = models.ForeignKey(ItineraryTemplate, on_delete=models.CASCADE, related_name="price_lines")
    description = models.CharField(max_length=200)
    quantity = models.PositiveIntegerField(default=1)
    unit_price = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    class Meta:
        ordering = ["pk"]

    def __str__(self):
        return self.description


class TripFeedback(TimeStampedModel):
    """The client's own rating of a finished trip, left on their trip page. Low scores alert the managers; high ones prompt a review request."""

    booking = models.OneToOneField(Booking, on_delete=models.CASCADE, related_name="feedback")
    rating = models.PositiveSmallIntegerField("Overall (1-5)")
    nps = models.PositiveSmallIntegerField("Would recommend (0-10)")
    guide_rating = models.PositiveSmallIntegerField("Guide / driver (1-5)", null=True, blank=True)
    lodge_rating = models.PositiveSmallIntegerField("Lodges (1-5)", null=True, blank=True)
    comment = models.TextField(blank=True)
    may_quote = models.BooleanField("OK to quote publicly", default=False)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.booking.reference} · {self.rating}/5"

    @property
    def kind(self) -> str:
        return "promoter" if self.nps >= 9 else ("passive" if self.nps >= 7 else "detractor")


class LetterTemplate(TimeStampedModel):
    """A standard letter or message the team sends again and again, with {tokens} filled in from a booking."""

    name = models.CharField(max_length=120)
    subject = models.CharField(max_length=160, blank=True)
    body = models.TextField()
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


# ---------------------------------------------------------------------------------------------- flights & travel reminders
class BookingFlight(models.Model):
    """A flight the client takes: their international arrival, a domestic hop to the Mara, the flight home."""

    ARRIVAL, INTERNAL, DEPARTURE = "arrival", "internal", "departure"
    LEGS = [(ARRIVAL, "Arrival into Kenya"), (INTERNAL, "Flight during the safari"), (DEPARTURE, "Flight home")]
    booking = models.ForeignKey(Booking, on_delete=models.CASCADE, related_name="flights")
    leg = models.CharField(max_length=10, choices=LEGS, default=ARRIVAL)
    airline = models.CharField(max_length=60)
    flight_number = models.CharField(max_length=20, blank=True)
    from_airport = models.CharField("From", max_length=60, blank=True)
    to_airport = models.CharField("To", max_length=60, blank=True)
    departs_at = models.DateTimeField(null=True, blank=True)
    arrives_at = models.DateTimeField(null=True, blank=True)
    pnr = models.CharField("Booking ref (PNR)", max_length=20, blank=True)
    notes = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["departs_at", "pk"]

    def __str__(self):
        return f"{self.airline} {self.flight_number}".strip()


class TravelReminder(models.Model):
    """One reminder sent before a trip, so each one goes once (to the agent and to the client)."""

    AGENT, CLIENT = "agent", "client"
    booking = models.ForeignKey(Booking, on_delete=models.CASCADE, related_name="travel_reminders")
    days_before = models.PositiveSmallIntegerField()
    audience = models.CharField(max_length=6, choices=[(AGENT, "Agent"), (CLIENT, "Client")])
    ok = models.BooleanField(default=True)
    detail = models.CharField(max_length=240, blank=True)
    sent_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-sent_at"]
        unique_together = [("booking", "days_before", "audience")]


# ---------------------------------------------------------------------------------------------- agent QR business cards
class AgentCard(models.Model):
    """An agent's digital business card. Its QR code opens a public page: save the contact, then tell us about the trip, which becomes their lead."""

    employee = models.OneToOneField("people.Employee", on_delete=models.CASCADE, related_name="card")
    token = models.CharField(max_length=24, unique=True, editable=False)
    headline = models.CharField(max_length=120, blank=True, help_text="e.g. Safari specialist · Maasai Mara & the coast")
    bio = models.CharField(max_length=400, blank=True, help_text="Two lines about you, shown on the card page")
    photo = models.ImageField(upload_to="cards/", blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return f"Card · {self.employee}"

    def save(self, *args, **kwargs):
        if not self.token:
            import secrets

            self.token = secrets.token_urlsafe(9)
        super().save(*args, **kwargs)

    @property
    def public_path(self) -> str:
        return reverse("public_card", args=[self.token])

    def stats(self) -> dict:
        from django.db.models import Count

        c = dict(self.events.values_list("kind").annotate(n=Count("pk")))
        leads = self.events.filter(kind=CardEvent.LEAD, enquiry__isnull=False)
        won = leads.filter(enquiry__booking__isnull=False).exclude(enquiry__booking__status=BookingStatus.CANCELLED).count()
        n = c.get(CardEvent.LEAD, 0)
        return {"scans": c.get(CardEvent.SCAN, 0), "saved": c.get(CardEvent.VCARD, 0), "leads": n, "won": won,
                "conversion": round(100 * won / n) if n else 0}


class CardEvent(models.Model):
    SCAN, VCARD, LEAD = "scan", "vcard", "lead"
    card = models.ForeignKey(AgentCard, on_delete=models.CASCADE, related_name="events")
    kind = models.CharField(max_length=5, choices=[(SCAN, "Opened the card"), (VCARD, "Saved the contact"), (LEAD, "Sent a trip request")], db_index=True)
    enquiry = models.ForeignKey("leads.Enquiry", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    visitor = models.CharField(max_length=64, blank=True, help_text="A hash of the visitor's address, so a refresh isn't counted twice")
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-created_at"]
