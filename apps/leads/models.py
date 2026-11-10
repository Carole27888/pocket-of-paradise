from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils import timezone
from simple_history.models import HistoricalRecords

from apps.core.models import TimeStampedModel


class Source(models.TextChoices):
    FACEBOOK = "facebook", "Facebook"
    INSTAGRAM = "instagram", "Instagram"
    TIKTOK = "tiktok", "TikTok"
    GOOGLE = "google", "Google"
    YOUTUBE = "youtube", "YouTube"
    WEBSITE = "website", "Website"
    WHATSAPP = "whatsapp", "WhatsApp"
    WALK_IN = "walk_in", "Walk-in"
    CALL = "call", "Call"
    DM = "dm", "DM"
    REFERRAL = "referral", "Referral"
    AGENT = "agent", "Travel agent partner"
    TRIPADVISOR = "tripadvisor", "TripAdvisor"
    AGENT_CARD = "agent_card", "Agent QR card"


# 2-letter badge code and colour token (see tokens.css) per source
SOURCE_BADGES = {
    "facebook": ("FB", "fb"), "instagram": ("IG", "ig"), "tiktok": ("TT", "tt"), "google": ("GO", "go"),
    "youtube": ("YT", "yt"), "website": ("WS", "ws"), "whatsapp": ("WA", "wa"), "walk_in": ("WI", "wi"),
    "call": ("CL", "cl"), "dm": ("DM", "dm"), "referral": ("RF", "rf"), "agent": ("TA", "agent"),
    "tripadvisor": ("TA", "ta"), "agent_card": ("QR", "rf"),
}


class CustomSource(models.Model):
    """A lead source the COO added on top of the built-in ones (an expo, a roadshow, a partner event, a radio ad)."""
    code = models.SlugField(max_length=30, unique=True, help_text="Used in reports and imports. Made from the name.")
    label = models.CharField("Name", max_length=60)
    badge = models.CharField("Short badge", max_length=3, blank=True, help_text="Two or three letters shown on lead badges.")
    is_active = models.BooleanField("In use", default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["label"]

    def __str__(self):
        return self.label

    @property
    def short(self):
        return (self.badge or self.label[:2]).upper()[:3]

    @property
    def lead_count(self):
        return Enquiry.objects.filter(source=self.code).count()


def custom_sources(active_only=True):
    try:
        qs = CustomSource.objects.all()
        if active_only:
            qs = qs.filter(is_active=True)
        return list(qs)
    except Exception:   # before the table exists (first migrate)
        return []


def source_choices():
    """Built-in sources first, then the ones the COO added. Use this, not Source.choices, anywhere a person picks."""
    return list(Source.choices) + [(c.code, c.label) for c in custom_sources()]


def source_labels():
    """Every code that ever existed (so old leads still read well), active or not."""
    return dict(Source.choices) | {c.code: c.label for c in custom_sources(active_only=False)}


def source_codes():
    return [c for c, _ in source_choices()]


class Stage(models.TextChoices):
    ENQUIRY = "enquiry", "Enquiry"
    QUOTED = "quoted", "Quoted"
    HOT = "hot", "Hot lead"
    ITINERARY = "itinerary", "Itinerary sent"
    DEPOSIT = "deposit", "Deposit paid"
    CONFIRMED = "confirmed", "Confirmed"
    COMPLETED = "completed", "Trip completed"
    LOST = "lost", "Lost"


OPEN_STAGES = [Stage.ENQUIRY, Stage.QUOTED, Stage.HOT, Stage.ITINERARY, Stage.DEPOSIT]


class Purpose(models.TextChoices):
    NEW = "new", "New booking"
    CUSTOM = "custom", "Custom itinerary request"
    GROUP = "group", "Group booking"
    REPEAT = "repeat", "Repeat client rebooking"
    AFTERSALES = "aftersales", "Complaint / after-sales"
    OTHER = "other", "Other"


class QuotationStatus(models.TextChoices):
    NOT_QUOTED = "not_quoted", "Not yet quoted"
    QUOTED = "quoted", "Quoted"
    REVISED = "revised", "Revised"


class Enquiry(TimeStampedModel):
    reference = models.CharField(max_length=16, unique=True, blank=True)
    client = models.ForeignKey("contacts.Client", on_delete=models.PROTECT, related_name="enquiries")
    package = models.ForeignKey(
        "catalog.TourPackage", null=True, blank=True, on_delete=models.SET_NULL, related_name="enquiries"
    )
    destination = models.ForeignKey(
        "catalog.Destination", null=True, blank=True, on_delete=models.SET_NULL, related_name="enquiries"
    )
    destination_text = models.CharField("Destination / interest", max_length=160, blank=True)
    duration_text = models.CharField(max_length=60, blank=True)
    budget_text = models.CharField(max_length=60, blank=True)
    adults = models.PositiveSmallIntegerField(default=2)
    children = models.PositiveSmallIntegerField(default=0)
    source = models.CharField(max_length=30, db_index=True)
    captured_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="captured_enquiries"
    )
    captured_via = models.CharField(max_length=20, default="front_desk",
                                    choices=[("marketing_sync", "Marketing sync"), ("front_desk", "Front Desk"),
                                             ("agent", "Agent"), ("api", "API / website")])
    purpose = models.CharField(max_length=12, choices=Purpose.choices, default=Purpose.NEW)
    dates_flexible = models.BooleanField(default=True)
    travel_date_from = models.DateField(null=True, blank=True)
    travel_date_to = models.DateField(null=True, blank=True)
    budget_currency = models.CharField(max_length=3, default="KES")
    budget_min_pp = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    budget_max_pp = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    notes = models.TextField(blank=True)
    brought_by = models.ForeignKey("people.Employee", null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="brought_enquiries", verbose_name="Who's that (brought the lead)",
                                   help_text="The staff member who met or brought this lead, e.g. who was at the expo stand.")
    remark = models.CharField(max_length=200, blank=True, help_text="One line the team should see in the list.")
    stage = models.CharField(max_length=10, choices=Stage.choices, default=Stage.ENQUIRY, db_index=True)
    lost_reason = models.CharField(max_length=160, blank=True)
    stage_changed_at = models.DateTimeField(default=timezone.now, db_index=True)
    last_contact_at = models.DateTimeField(null=True, blank=True)
    next_action = models.CharField(max_length=160, blank=True)
    next_action_due = models.DateTimeField(null=True, blank=True)
    quotation_status = models.CharField(max_length=10, choices=QuotationStatus.choices,
                                        default=QuotationStatus.NOT_QUOTED, db_index=True)
    assigned_agent = models.ForeignKey(
        "people.Employee", null=True, blank=True, on_delete=models.SET_NULL, related_name="enquiries", db_index=True
    )
    deal_value = models.DecimalField("Estimated deal value (KES)", max_digits=14, decimal_places=2, default=0,
                                     help_text="Latest quotation or booking total in KES; drives pipeline value.")
    is_returning_client = models.BooleanField(default=False)
    double_allocated = models.BooleanField(default=False, db_index=True)
    campaign = models.ForeignKey("marketing.Campaign", null=True, blank=True, on_delete=models.SET_NULL,
                                 related_name="enquiries")
    tracked_link = models.ForeignKey("marketing.TrackedLink", null=True, blank=True, on_delete=models.SET_NULL,
                                     related_name="enquiries")

    history = HistoricalRecords()

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "enquiries"
        indexes = [models.Index(fields=["stage", "assigned_agent"]), models.Index(fields=["source", "created_at"])]

    def __str__(self):
        return f"{self.reference} · {self.client}"

    def save(self, *args, **kwargs):
        from apps.core.models import Sequence

        if not self.reference:
            self.reference = Sequence.next_number("enquiry", "ENQ", 5)
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse("leads:detail", args=[self.pk])

    @property
    def brought_by_label(self):
        """Who brought the lead: the person named on it, else the staff member who captured it."""
        if self.brought_by_id:
            return self.brought_by.short_name
        emp = getattr(self.captured_by, "employee", None) if self.captured_by_id else None
        return emp.short_name if emp else (self.captured_by.full_name if self.captured_by_id else "")

    @property
    def travel_dates_label(self):
        if self.travel_date_from and self.travel_date_to:
            return f"{self.travel_date_from:%d %b} – {self.travel_date_to:%d %b %Y}"
        if self.travel_date_from:
            return f"{self.travel_date_from:%d %b %Y}"
        return "Flexible" if self.dates_flexible else "—"

    @property
    def is_unassigned(self):
        return self.assigned_agent_id is None

    @property
    def party_size(self):
        return self.adults + self.children

    @property
    def party_label(self):
        s = f"{self.adults} adult{'s' if self.adults != 1 else ''}"
        if self.children:
            s += f", {self.children} child{'ren' if self.children != 1 else ''}"
        return s

    @property
    def interest_label(self):
        if self.package_id:
            return self.package.title
        if self.destination_id:
            return self.destination.name
        return self.destination_text or "—"

    @property
    def days_in_stage(self) -> int:
        return (timezone.now() - self.stage_changed_at).days

    @property
    def channel_label(self):
        if self.source in ("facebook", "instagram", "tiktok", "youtube"):
            return "Social media"
        if self.captured_via == "front_desk" or self.source in ("walk_in", "call"):
            return "Front desk"
        return {"whatsapp": "WhatsApp bot", "website": "Website & Google", "google": "Website & Google"}.get(
            self.source, "Other")

    @property
    def captured_by_label(self):
        return str(self.captured_by) if self.captured_by_id else self.get_captured_via_display()

    def get_source_display(self):
        return source_labels().get(self.source, self.source)

    @property
    def source_badge(self):
        if self.source in SOURCE_BADGES:
            return SOURCE_BADGES[self.source]
        c = next((c for c in custom_sources(False) if c.code == self.source), None)
        return (c.short, "muted") if c else ("??", "muted")


class LeadAssignment(models.Model):
    METHODS = [("manual", "Manual"), ("round_robin", "Round robin"), ("rule", "Rule"), ("owner", "Client owner"),
               ("auto", "Auto"), ("card", "Agent's QR card")]
    enquiry = models.ForeignKey(Enquiry, on_delete=models.CASCADE, related_name="assignments")
    agent = models.ForeignKey("people.Employee", on_delete=models.CASCADE, related_name="lead_assignments")
    assigned_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    assigned_at = models.DateTimeField(default=timezone.now, db_index=True)
    method = models.CharField(max_length=12, choices=METHODS, default="manual")
    active = models.BooleanField(default=True, db_index=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    end_reason = models.CharField(max_length=160, blank=True)

    class Meta:
        ordering = ["-assigned_at"]

    def __str__(self):
        return f"{self.enquiry.reference} → {self.agent}"


class LeadActivity(models.Model):
    TYPES = [
        ("call", "Call"), ("whatsapp", "WhatsApp"), ("sms", "SMS"), ("email", "Email"),
        ("quotation", "Quotation sent"), ("note", "Note"), ("stage", "Stage change"), ("assignment", "Assignment"),
        ("created", "Enquiry created"),
    ]
    CONTACT_TYPES = {"call", "whatsapp", "sms", "email", "quotation"}
    enquiry = models.ForeignKey(Enquiry, on_delete=models.CASCADE, related_name="activities")
    activity_type = models.CharField(max_length=12, choices=TYPES)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    timestamp = models.DateTimeField(default=timezone.now, db_index=True)
    details = models.TextField(blank=True)

    class Meta:
        ordering = ["-timestamp", "-pk"]
        verbose_name_plural = "lead activities"

    def __str__(self):
        return f"{self.get_activity_type_display()} · {self.enquiry.reference}"
