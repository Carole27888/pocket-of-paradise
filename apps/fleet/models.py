from datetime import timedelta

from django.db import models
from django.utils import timezone

from apps.core.models import TimeStampedModel


class Vehicle(TimeStampedModel):
    TYPE_CHOICES = [
        ("van", "Safari van 4x4 pop-top"),
        ("cruiser", "Land Cruiser"),
        ("minibus", "Minibus"),
        ("coaster", "Coaster"),
    ]
    AVAILABLE, OUT, MAINTENANCE, OUT_OF_SERVICE = "available", "out", "maintenance", "oos"
    STATUS_CHOICES = [
        (AVAILABLE, "Available"),
        (OUT, "Out on trip"),
        (MAINTENANCE, "In maintenance"),
        (OUT_OF_SERVICE, "Out of service"),
    ]

    registration_no = models.CharField(max_length=20, unique=True)
    nickname = models.CharField(max_length=40, blank=True)
    vehicle_type = models.CharField(max_length=10, choices=TYPE_CHOICES, default="van")
    make_model = models.CharField(max_length=60, blank=True)
    year = models.PositiveSmallIntegerField(null=True, blank=True)
    seats = models.PositiveSmallIntegerField(default=7)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=AVAILABLE, db_index=True)
    current_driver = models.ForeignKey(
        "people.Employee", null=True, blank=True, on_delete=models.SET_NULL, related_name="vehicles",
        limit_choices_to={"is_driver": True},
    )
    odometer_km = models.PositiveIntegerField(default=0)
    insurance_expiry = models.DateField(null=True, blank=True)
    inspection_expiry = models.DateField(null=True, blank=True)
    last_service_date = models.DateField(null=True, blank=True)
    next_service_due = models.DateField(null=True, blank=True)
    next_service_km = models.PositiveIntegerField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["registration_no"]

    def __str__(self):
        return f"{self.registration_no}" + (f" · {self.nickname}" if self.nickname else "")

    def get_absolute_url(self):
        from django.urls import reverse

        return reverse("fleet:detail", args=[self.pk])

    @property
    def is_assignable(self) -> bool:
        return self.status == self.AVAILABLE

    def expiry_flags(self, within_days: int = 30) -> list[dict]:
        """Document/service items expiring soon or overdue."""
        today = timezone.localdate()
        out = []
        for label, d in (("Insurance", self.insurance_expiry), ("Inspection", self.inspection_expiry),
                         ("Service", self.next_service_due)):
            if d and d <= today + timedelta(days=within_days):
                out.append({"label": label, "date": d, "overdue": d < today, "days": (d - today).days})
        return out


class FuelLog(TimeStampedModel):
    vehicle = models.ForeignKey(Vehicle, on_delete=models.CASCADE, related_name="fuel_logs")
    date = models.DateField(default=timezone.localdate)
    litres = models.DecimalField(max_digits=8, decimal_places=2)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    currency = models.CharField(max_length=3, default="KES")
    odometer_km = models.PositiveIntegerField(null=True, blank=True)
    station = models.CharField(max_length=80, blank=True)
    filled_by = models.ForeignKey("people.Employee", null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        ordering = ["-date", "-pk"]

    def __str__(self):
        return f"{self.vehicle} {self.date} {self.litres}L"


class MaintenanceLog(TimeStampedModel):
    KINDS = [("service", "Routine service"), ("repair", "Repair"), ("tyres", "Tyres"), ("inspection", "Inspection"),
             ("accident", "Accident damage"), ("other", "Other")]
    vehicle = models.ForeignKey(Vehicle, on_delete=models.CASCADE, related_name="maintenance_logs")
    date = models.DateField(default=timezone.localdate)
    kind = models.CharField(max_length=10, choices=KINDS, default="service")
    description = models.CharField(max_length=200)
    garage = models.CharField(max_length=100, blank=True)
    cost = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    odometer_km = models.PositiveIntegerField(null=True, blank=True)
    next_service_due = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["-date", "-pk"]

    def __str__(self):
        return f"{self.vehicle} {self.date} {self.get_kind_display()}"
