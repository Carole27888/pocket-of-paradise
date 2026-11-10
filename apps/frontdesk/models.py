from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils import timezone

from apps.core.models import TimeStampedModel

# Enquiry sources the reservations desk captures itself (the four capture queues).
DESK_SOURCES = ["whatsapp", "walk_in", "call", "dm"]

FUEL_LEVELS = [("E", "Empty"), ("1/4", "¼"), ("1/2", "½"), ("3/4", "¾"), ("F", "Full")]


class MovementQuerySet(models.QuerySet):
    def open(self):
        return self.filter(checked_in_at__isnull=True)

    def late(self, now=None):
        return self.open().filter(expected_return_at__lt=now or timezone.now())


class TripMovement(TimeStampedModel):
    """One check-out → check-in of a vehicle and/or its driver and guide."""

    PURPOSES = [("safari", "Safari / tour"), ("transfer", "Airport / hotel transfer"), ("excursion", "Day excursion"),
                ("service", "Service / garage"), ("errand", "Office errand"), ("shoot", "Content shoot")]
    CONDITION_OK, CONDITION_SERVICE, CONDITION_DAMAGED = "ok", "service", "damaged"
    CONDITIONS = [(CONDITION_OK, "Good, ready for the next trip"), (CONDITION_SERVICE, "Needs service"),
                  (CONDITION_DAMAGED, "Damaged / not roadworthy")]

    reference = models.CharField(max_length=16, unique=True, blank=True)
    vehicle = models.ForeignKey("fleet.Vehicle", null=True, blank=True, on_delete=models.PROTECT,
                                related_name="movements")
    driver = models.ForeignKey("people.Employee", null=True, blank=True, on_delete=models.PROTECT,
                               related_name="driven_movements")
    guide = models.ForeignKey("people.Employee", null=True, blank=True, on_delete=models.PROTECT,
                              related_name="guided_movements")
    enquiry = models.ForeignKey("leads.Enquiry", null=True, blank=True, on_delete=models.SET_NULL,
                                related_name="movements")
    destination = models.ForeignKey("catalog.Destination", null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="movements")
    destination_text = models.CharField("Going to", max_length=160, blank=True)
    purpose = models.CharField(max_length=10, choices=PURPOSES, default="safari")
    passengers = models.PositiveSmallIntegerField(default=0)
    checked_out_at = models.DateTimeField(default=timezone.now, db_index=True)
    expected_return_at = models.DateTimeField(db_index=True)
    checked_out_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                       related_name="+")
    odometer_out = models.PositiveIntegerField(null=True, blank=True)
    fuel_out = models.CharField(max_length=3, choices=FUEL_LEVELS, blank=True)
    notes_out = models.TextField("Notes at check-out", blank=True)
    checked_in_at = models.DateTimeField(null=True, blank=True, db_index=True)
    checked_in_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                      related_name="+")
    odometer_in = models.PositiveIntegerField(null=True, blank=True)
    fuel_in = models.CharField(max_length=3, choices=FUEL_LEVELS, blank=True)
    condition_in = models.CharField("Condition on return", max_length=8, choices=CONDITIONS, blank=True)
    notes_in = models.TextField("Notes at check-in", blank=True)
    late_alerted_at = models.DateTimeField(null=True, blank=True)

    objects = MovementQuerySet.as_manager()

    class Meta:
        ordering = ["-checked_out_at", "-pk"]
        indexes = [models.Index(fields=["checked_in_at", "expected_return_at"])]

    def __str__(self):
        return f"{self.reference} · {self.subject_label}"

    def save(self, *args, **kwargs):
        from apps.core.models import Sequence

        if not self.reference:
            self.reference = Sequence.next_number("movement", "MOV", 5)
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse("frontdesk:movement_detail", args=[self.pk])

    @property
    def is_open(self) -> bool:
        return self.checked_in_at is None

    @property
    def is_late(self) -> bool:
        return self.is_open and self.expected_return_at < timezone.now()

    @property
    def returned_late(self) -> bool:
        return not self.is_open and self.checked_in_at > self.expected_return_at

    @property
    def minutes_late(self) -> int:
        end = self.checked_in_at or timezone.now()
        return max(0, int((end - self.expected_return_at).total_seconds() // 60))

    @property
    def late_label(self) -> str:
        m = self.minutes_late
        if m < 60:
            return f"{m} min"
        h, m = divmod(m, 60)
        if h < 24:
            return f"{h}h {m:02d}m"
        return f"{h // 24}d {h % 24}h"

    @property
    def distance_km(self):
        if self.odometer_in is not None and self.odometer_out is not None:
            return self.odometer_in - self.odometer_out
        return None

    @property
    def destination_label(self) -> str:
        if self.destination_id:
            return self.destination.name
        return self.destination_text or self.get_purpose_display()

    @property
    def subject_label(self) -> str:
        if self.vehicle_id:
            return self.vehicle.registration_no
        people = [p.short_name for p in (self.driver, self.guide) if p]
        return " & ".join(people) or "—"

    @property
    def crew(self):
        seen, out = set(), []
        for p in (self.driver, self.guide):
            if p and p.pk not in seen:
                seen.add(p.pk)
                out.append(p)
        return out


class Visitor(TimeStampedModel):
    name = models.CharField(max_length=120)
    phone_or_company = models.CharField("Phone / company", max_length=120, blank=True)
    reason = models.CharField(max_length=200)
    host = models.ForeignKey("people.Employee", null=True, blank=True, on_delete=models.SET_NULL, related_name="visitors")
    badge_no = models.CharField(max_length=10, blank=True)
    time_in = models.DateTimeField(default=timezone.now, db_index=True)
    time_out = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-time_in"]

    def __str__(self):
        return self.name

    @property
    def is_in(self) -> bool:
        return self.time_out is None


class Appointment(TimeStampedModel):
    STATUSES = [("scheduled", "Scheduled"), ("arrived", "Arrived"), ("done", "Done"), ("cancelled", "Cancelled")]
    title = models.CharField(max_length=160)
    people = models.CharField(max_length=200, blank=True, help_text="Who is coming")
    host = models.ForeignKey("people.Employee", null=True, blank=True, on_delete=models.SET_NULL, related_name="appointments")
    start = models.DateTimeField(db_index=True)
    end = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=STATUSES, default="scheduled")
    location = models.CharField(max_length=120, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["start"]

    def __str__(self):
        return self.title

    @property
    def payload_json(self) -> str:
        """Everything the details pop-up shows, as JSON for the page to read."""
        import json

        from django.utils import timezone

        st, en = timezone.localtime(self.start), timezone.localtime(self.end) if self.end else None
        return json.dumps({"id": self.pk, "title": self.title, "people": self.people, "host": self.host.full_name if self.host_id else "", "host_id": self.host_id or "",
                           "when": f"{st:%A %d %B %Y, %I:%M %p}" + (f" – {en:%I:%M %p}" if en else ""), "start": f"{st:%Y-%m-%dT%H:%M}", "status": self.status,
                           "status_label": self.get_status_display(), "location": self.location, "notes": self.notes, "created": f"{timezone.localtime(self.created_at):%d %b %Y, %I:%M %p}"})


class EquipmentRecord(TimeStampedModel):
    """Radios, cooler boxes, tents, binoculars, first-aid kits: who has what, and when it is due back."""

    ITEMS = [("radio", "Radio"), ("cooler", "Cooler box"), ("tent", "Tent"), ("binoculars", "Binoculars"),
             ("firstaid", "First-aid kit"), ("fridge", "Car fridge"), ("other", "Other")]
    item = models.CharField(max_length=10, choices=ITEMS)
    code = models.CharField(max_length=20, help_text="e.g. RAD-04")
    assigned_to = models.ForeignKey("people.Employee", null=True, blank=True, on_delete=models.SET_NULL, related_name="equipment")
    taken_at = models.DateTimeField(null=True, blank=True)
    due_back = models.DateTimeField(null=True, blank=True)
    returned_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["item", "code"]
        unique_together = [("item", "code")]

    def __str__(self):
        return f"{self.get_item_display()} {self.code}"

    @property
    def state(self) -> str:
        if self.assigned_to_id and not self.returned_at:
            return "Overdue" if self.due_back and self.due_back < timezone.now() else "Out"
        return "In store"


class ShiftNote(TimeStampedModel):
    note = models.TextField()
    author_name = models.CharField(max_length=120, blank=True)
    date = models.DateField(default=timezone.localdate, db_index=True)

    class Meta:
        ordering = ["-created_at"]



class BusinessCard(TimeStampedModel):
    """A business card handed to the desk: who they are, how to reach them, and what kind of contact they are."""

    CATEGORIES = [("supplier", "Supplier / lodge"), ("tour_operator", "Tour operator / agent"), ("client", "Client"), ("corporate", "Corporate / events"),
                  ("partner", "Partner"), ("media", "Media / influencer"), ("government", "Government / parks"), ("other", "Other")]

    name = models.CharField(max_length=120)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=30, blank=True)
    category = models.CharField(max_length=14, choices=CATEGORIES, default="other", db_index=True)
    company = models.CharField(max_length=120, blank=True)
    job_title = models.CharField(max_length=80, blank=True)
    notes = models.CharField(max_length=300, blank=True)
    card_image = models.ImageField("Photo of the card", upload_to="business-cards/%Y/%m/", blank=True)
    marketing_opt_out = models.BooleanField("Do not send bulk messages", default=False)
    added_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name}" + (f" · {self.company}" if self.company else "")

    def get_absolute_url(self):
        return reverse("frontdesk:card_edit", args=[self.pk])

    def save(self, *a, **kw):
        from apps.core.phone import normalise_phone

        self.phone = normalise_phone(self.phone) if self.phone else ""
        self.email = (self.email or "").strip().lower()
        super().save(*a, **kw)
