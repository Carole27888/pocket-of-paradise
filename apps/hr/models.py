from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils import timezone
from simple_history.models import HistoricalRecords

from apps.core.models import TimeStampedModel


class LeaveType(models.Model):
    """Admin-editable: nothing about leave types is hard-coded."""

    code = models.SlugField(max_length=20, unique=True)
    name = models.CharField(max_length=60)
    paid = models.BooleanField(default=True)
    capped = models.BooleanField(default=True, help_text="Untick for leave with no entitlement limit (e.g. unpaid)")
    default_days = models.DecimalField("Default entitlement (days / year)", max_digits=5, decimal_places=1, default=0)
    requires_document = models.BooleanField(default=False, help_text="e.g. a doctor's note")
    statutory = models.BooleanField(default=False, help_text="Exists because the Employment Act requires it (vs a company extra)")
    calendar_days = models.BooleanField(default=False, help_text="Count every calendar day (maternity); otherwise working days only")
    active = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=10)

    class Meta:
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name


class PublicHoliday(models.Model):
    date = models.DateField(unique=True)
    name = models.CharField(max_length=80)

    class Meta:
        ordering = ["date"]

    def __str__(self):
        return f"{self.name} ({self.date:%d %b %Y})"


class LeavePolicy(models.Model):
    """Single row."""

    year_start_month = models.PositiveSmallIntegerField(default=1, help_text="Month the leave year starts (1 = January)")
    carry_forward_cap = models.DecimalField(max_digits=5, decimal_places=1, default=5, help_text="Most days of unused annual leave that roll over")
    carry_forward_type = models.SlugField(max_length=20, default="annual", help_text="Leave type code that carries forward")

    class Meta:
        verbose_name_plural = "leave policy"

    @classmethod
    def get(cls) -> "LeavePolicy":
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class LeaveEntitlement(models.Model):
    employee = models.ForeignKey("people.Employee", on_delete=models.CASCADE, related_name="leave_entitlements")
    leave_type = models.ForeignKey(LeaveType, on_delete=models.CASCADE, related_name="entitlements")
    year = models.PositiveSmallIntegerField()
    days = models.DecimalField(max_digits=5, decimal_places=1)
    carried_forward = models.DecimalField(max_digits=5, decimal_places=1, default=0)

    class Meta:
        unique_together = [("employee", "leave_type", "year")]
        ordering = ["-year", "employee__full_name"]


class LeaveRequest(TimeStampedModel):
    PENDING, APPROVED, REJECTED, CANCEL_REQ, CANCELLED, WITHDRAWN = "pending", "approved", "rejected", "cancel_requested", "cancelled", "withdrawn"
    STATUSES = [(PENDING, "Pending"), (APPROVED, "Approved"), (REJECTED, "Rejected"), (CANCEL_REQ, "Cancellation requested"),
                (CANCELLED, "Cancelled"), (WITHDRAWN, "Withdrawn")]
    # statuses whose days count against the balance
    COUNTED = (APPROVED, CANCEL_REQ)

    employee = models.ForeignKey("people.Employee", on_delete=models.CASCADE, related_name="leave_requests")
    leave_type = models.ForeignKey(LeaveType, on_delete=models.PROTECT, related_name="requests")
    start_date = models.DateField(db_index=True)
    end_date = models.DateField()
    half_day = models.CharField(max_length=2, blank=True, choices=[("", "Full day(s)"), ("am", "Morning only"), ("pm", "Afternoon only")])
    days = models.DecimalField(max_digits=5, decimal_places=1, help_text="Snapshot taken at submission: later holiday edits never change it")
    reason = models.CharField(max_length=240, blank=True)
    document = models.FileField(upload_to="leave/%Y/%m/", blank=True)
    status = models.CharField(max_length=16, choices=STATUSES, default=PENDING, db_index=True)
    approver = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.CharField(max_length=240, blank=True)
    cancel_reason = models.CharField(max_length=240, blank=True)

    history = HistoricalRecords()

    class Meta:
        ordering = ["-start_date", "-pk"]

    def __str__(self):
        return f"{self.employee} · {self.leave_type} · {self.start_date}"

    def get_absolute_url(self):
        return reverse("hr:my_leave")

    @property
    def is_open(self) -> bool:
        return self.status in (self.PENDING, self.APPROVED, self.CANCEL_REQ)



class OfficeLocation(models.Model):
    """A place staff clock in from. Distance to the nearest active office decides "in office" vs "away"."""

    name = models.CharField(max_length=80)
    latitude = models.DecimalField(max_digits=9, decimal_places=6)
    longitude = models.DecimalField(max_digits=9, decimal_places=6)
    radius_m = models.PositiveIntegerField("Radius (metres)", default=150, help_text="Within this distance counts as being at the office")
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class AttendancePolicy(models.Model):
    """One row. Off by default: staff can clock in from anywhere and the record shows how far from the office they were."""

    require_office = models.BooleanField("Only allow clock-in at an office", default=False)
    require_location = models.BooleanField("Require location to be shared", default=False)
    workday_start = models.TimeField(default="08:00")
    grace_minutes = models.PositiveSmallIntegerField(default=15, help_text="Clocking in later than start + grace is marked late")

    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class AttendanceRecord(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="attendance")
    date = models.DateField(db_index=True)
    clock_in_at = models.DateTimeField()
    in_lat = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    in_lng = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    in_accuracy_m = models.PositiveIntegerField(null=True, blank=True)
    in_office = models.ForeignKey(OfficeLocation, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    in_distance_m = models.PositiveIntegerField(null=True, blank=True, help_text="Metres from the nearest office")
    in_at_office = models.BooleanField(default=False)
    late = models.BooleanField(default=False)
    clock_out_at = models.DateTimeField(null=True, blank=True)
    out_lat = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    out_lng = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    out_accuracy_m = models.PositiveIntegerField(null=True, blank=True)
    out_distance_m = models.PositiveIntegerField(null=True, blank=True)
    out_at_office = models.BooleanField(default=False)
    note = models.CharField(max_length=160, blank=True)

    class Meta:
        ordering = ["-clock_in_at"]
        indexes = [models.Index(fields=["user", "date"])]

    @property
    def hours(self):
        end = self.clock_out_at or timezone.now()
        return round((end - self.clock_in_at).total_seconds() / 3600, 2)

    @property
    def open(self) -> bool:
        return self.clock_out_at is None

    @staticmethod
    def map_link(lat, lng) -> str:
        return f"https://www.google.com/maps?q={lat},{lng}" if lat is not None and lng is not None else ""

    @property
    def in_map(self) -> str:
        return self.map_link(self.in_lat, self.in_lng)

    @property
    def out_map(self) -> str:
        return self.map_link(self.out_lat, self.out_lng)
