from datetime import timedelta

from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils import timezone
from simple_history.models import HistoricalRecords

from apps.core.fields import EncryptedCharField
from apps.core.models import Sequence, TimeStampedModel


class Stage(models.TextChoices):
    PLANNED = "planned", "Planned"
    READY = "ready", "Resources ready"
    DEPARTED = "departed", "Departed"
    COMPLETED = "completed", "Completed"
    CANCELLED = "cancelled", "Cancelled"


STAGE_TONE = {"planned": "warning", "ready": "info", "departed": "success", "completed": "success",
              "cancelled": "danger"}
# Resources that must be confirmed before a departure can be marked "Resources ready".
CHECKLIST = [("accommodation_confirmed", "Accommodation confirmed"), ("permits_issued", "Park permits issued"),
             ("park_fees_paid", "Park fees paid"), ("meals_arranged", "Meals arranged"),
             ("insurance_ok", "Insurance in place")]


class Departure(TimeStampedModel):
    """One dated run of a tour package: the unit that has a guide, a vehicle, a manifest and a readiness state."""

    reference = models.CharField(max_length=16, unique=True, blank=True)
    package = models.ForeignKey("catalog.TourPackage", on_delete=models.PROTECT, related_name="departures")
    start_date = models.DateField(db_index=True)
    end_date = models.DateField()
    capacity = models.PositiveSmallIntegerField(default=7)
    stage = models.CharField(max_length=10, choices=Stage.choices, default=Stage.PLANNED, db_index=True)
    guide = models.ForeignKey("people.Employee", null=True, blank=True, on_delete=models.SET_NULL,
                              related_name="guided_departures")
    driver = models.ForeignKey("people.Employee", null=True, blank=True, on_delete=models.SET_NULL,
                               related_name="driven_departures")
    vehicle = models.ForeignKey("fleet.Vehicle", null=True, blank=True, on_delete=models.SET_NULL,
                                related_name="departures")
    meeting_point = models.CharField(max_length=160, blank=True, default="Pocket of Paradise office, Nairobi")
    pickup_time = models.TimeField(null=True, blank=True)
    notes = models.TextField(blank=True)
    accommodation_confirmed = models.BooleanField(default=False)
    permits_issued = models.BooleanField(default=False)
    park_fees_paid = models.BooleanField(default=False)
    meals_arranged = models.BooleanField(default=False)
    insurance_ok = models.BooleanField("Insurance in place", default=False)
    resources_confirmed_at = models.DateTimeField(null=True, blank=True)
    resources_confirmed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                               on_delete=models.SET_NULL, related_name="+")
    departed_at = models.DateTimeField(null=True, blank=True)
    departed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+")
    completed_at = models.DateTimeField(null=True, blank=True)
    completed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                     related_name="+")
    stage_changed_at = models.DateTimeField(default=timezone.now)
    delay_reason = models.CharField(max_length=200, blank=True)

    history = HistoricalRecords()

    class Meta:
        ordering = ["start_date", "pk"]
        indexes = [models.Index(fields=["stage", "start_date"])]

    def __str__(self):
        return f"{self.reference} · {self.package.title}"

    def save(self, *args, **kwargs):
        if not self.reference:
            self.reference = Sequence.next_number("departure", "DEP", 4)
        if not self.end_date:
            self.end_date = self.start_date + timedelta(days=max(self.package.days - 1, 0))
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse("trips:detail", args=[self.pk])

    # ---- seats
    @property
    def live_bookings(self):
        from apps.sales.models import BookingStatus

        return self.bookings.exclude(status=BookingStatus.CANCELLED)

    @property
    def seats_sold(self) -> int:
        return sum(b.adults + b.children for b in self.live_bookings)

    @property
    def seats_left(self) -> int:
        return max(0, self.capacity - self.seats_sold)

    @property
    def fill_percent(self) -> int:
        return min(100, round(100 * self.seats_sold / self.capacity)) if self.capacity else 0

    # ---- readiness
    @property
    def days_to_departure(self) -> int:
        return (self.start_date - timezone.localdate()).days

    @property
    def days_in_stage(self) -> int:
        return (timezone.now() - self.stage_changed_at).days

    @property
    def is_open(self) -> bool:
        return self.stage in (Stage.PLANNED, Stage.READY)

    def missing_resources(self) -> list[str]:
        out = []
        if not self.guide_id:
            out.append("Guide")
        if not self.driver_id:
            out.append("Driver")
        if not self.vehicle_id:
            out.append("Vehicle")
        out += [label for field, label in CHECKLIST if not getattr(self, field)]
        return out

    @property
    def checklist(self) -> list[dict]:
        items = [("Guide", self.guide_id), ("Driver", self.driver_id), ("Vehicle", self.vehicle_id)]
        items += [(label, getattr(self, field)) for field, label in CHECKLIST]
        return [{"label": l, "done": bool(v)} for l, v in items]

    @property
    def checklist_percent(self) -> int:
        c = self.checklist
        return round(100 * sum(1 for i in c if i["done"]) / len(c))

    def attention(self, stuck_days: int | None = None) -> list[str]:
        """Why this departure needs a human today (empty when fine)."""
        stuck_days = stuck_days if stuck_days is not None else settings.TRIPS_STUCK_DAYS
        reasons = []
        if self.stage == Stage.PLANNED and self.days_to_departure <= stuck_days:
            miss = self.missing_resources()
            if miss:
                reasons.append(("Missing " + ", ".join(miss[:3]).lower() + (f" +{len(miss) - 3}" if len(miss) > 3 else ""))
                               + (" · departs today" if self.days_to_departure <= 0 else f" · departs in {self.days_to_departure}d"))
        if self.stage == Stage.READY and self.days_to_departure < 0:
            reasons.append(f"Should have departed {-self.days_to_departure}d ago")
        if self.stage in (Stage.PLANNED, Stage.READY) and 0 <= self.days_to_departure <= 2 and not self.manifest.exists():
            reasons.append("No manifest yet")
        if self.stage == Stage.DEPARTED and self.end_date < timezone.localdate():
            reasons.append("Trip ended: mark completed")
        if self.delay_reason:
            reasons.append(f"Delayed: {self.delay_reason}")
        return reasons


class DepartureEvent(models.Model):
    departure = models.ForeignKey(Departure, on_delete=models.CASCADE, related_name="events")
    kind = models.CharField(max_length=20)
    text = models.CharField(max_length=240)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                             related_name="+")
    at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-at", "-pk"]


class ManifestEntry(models.Model):
    departure = models.ForeignKey(Departure, on_delete=models.CASCADE, related_name="manifest")
    booking = models.ForeignKey("sales.Booking", null=True, blank=True, on_delete=models.SET_NULL,
                                related_name="manifest_entries")
    full_name = models.CharField(max_length=120)
    is_child = models.BooleanField(default=False)
    age = models.PositiveSmallIntegerField(null=True, blank=True)
    nationality = models.CharField(max_length=60, blank=True)
    passport_number = EncryptedCharField(blank=True)
    phone = models.CharField(max_length=20, blank=True)
    dietary_notes = models.CharField(max_length=200, blank=True)
    medical_notes = models.CharField(max_length=200, blank=True)
    emergency_contact = models.CharField(max_length=160, blank=True)
    pickup_point = models.CharField(max_length=120, blank=True)
    checked_in_at = models.DateTimeField(null=True, blank=True)
    checked_in_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                      related_name="+")
    is_lead = models.BooleanField(default=False)

    class Meta:
        ordering = ["booking_id", "-is_lead", "pk"]

    def __str__(self):
        return self.full_name

    @property
    def masked_passport(self):
        p = self.passport_number or ""
        return f"••••{p[-3:]}" if len(p) > 3 else ("•••" if p else "")


class Incident(TimeStampedModel):
    TYPES = [("vehicle", "Vehicle breakdown / accident"), ("medical", "Medical"), ("guest", "Guest complaint"),
             ("weather", "Weather / road"), ("wildlife", "Wildlife"), ("security", "Security"), ("other", "Other")]
    SEVERITY = [("low", "Low"), ("medium", "Medium"), ("high", "High")]

    departure = models.ForeignKey(Departure, on_delete=models.CASCADE, related_name="incidents")
    guide = models.ForeignKey("people.Employee", null=True, blank=True, on_delete=models.SET_NULL,
                              related_name="incidents")
    kind = models.CharField(max_length=10, choices=TYPES, default="other")
    severity = models.CharField(max_length=6, choices=SEVERITY, default="low")
    description = models.TextField()
    action_taken = models.TextField(blank=True)
    reported_at = models.DateTimeField(default=timezone.now, db_index=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    history = HistoricalRecords()

    class Meta:
        ordering = ["-reported_at"]

    def __str__(self):
        return f"{self.get_kind_display()} · {self.departure.reference}"

    def get_absolute_url(self):
        return reverse("trips:incident", args=[self.pk])

    @property
    def is_open(self):
        return self.resolved_at is None


# ---- computed columns used by the list screens
from decimal import Decimal  # noqa: F401
Departure.seats_label = property(lambda self: f"{self.seats_sold}/{self.capacity}")
Incident.status_label = property(lambda self: "Open" if self.is_open else "Resolved")
