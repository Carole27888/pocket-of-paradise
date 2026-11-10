from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.models import TimeStampedModel
from apps.core.phone import normalise_phone


class Department(TimeStampedModel):
    name = models.CharField(max_length=80, unique=True)
    description = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Specialty(models.Model):
    """Destination/segment specialties used by the assignment engine."""

    name = models.CharField(max_length=60, unique=True)
    slug = models.SlugField(max_length=60, unique=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "specialties"

    def __str__(self):
        return self.name


class Employee(TimeStampedModel):
    IN_OFFICE, OUT_ON_TRIP, OFF, ON_LEAVE = "in_office", "out_on_trip", "off", "on_leave"
    DUTY_CHOICES = [(IN_OFFICE, "In office"), (OUT_ON_TRIP, "Out on trip"), (OFF, "Off"), (ON_LEAVE, "On leave")]
    PAYOUT_CHOICES = [("bank", "Bank"), ("mpesa", "M-PESA")]

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="employee"
    )
    full_name = models.CharField(max_length=120)
    initials = models.CharField(max_length=4, blank=True)
    phone = models.CharField(max_length=20, blank=True, db_index=True)
    email = models.EmailField(blank=True)
    title = models.CharField("Role / title", max_length=80, blank=True)
    department = models.ForeignKey(Department, null=True, blank=True, on_delete=models.SET_NULL, related_name="staff")
    is_sales_agent = models.BooleanField(default=False)
    is_guide = models.BooleanField(default=False)
    is_driver = models.BooleanField(default=False)
    daily_enquiry_limit = models.PositiveSmallIntegerField(default=15)
    specialties = models.ManyToManyField(Specialty, blank=True, related_name="employees")
    languages = models.CharField(max_length=120, blank=True, help_text="Comma separated, e.g. English, Swahili, German")
    on_duty = models.CharField(max_length=12, choices=DUTY_CHOICES, default=IN_OFFICE, db_index=True)
    leave_from = models.DateField(null=True, blank=True)
    leave_to = models.DateField(null=True, blank=True)
    basic_salary = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    salary_currency = models.CharField(max_length=3, default="KES")
    payout_method = models.CharField(max_length=6, choices=PAYOUT_CHOICES, default="bank")
    bank_name = models.CharField(max_length=80, blank=True)
    bank_account = models.CharField(max_length=40, blank=True)
    mpesa_number = models.CharField(max_length=20, blank=True)
    kra_pin = models.CharField("KRA PIN", max_length=20, blank=True)
    nssf_no = models.CharField("NSSF no.", max_length=20, blank=True)
    shif_no = models.CharField("SHIF no.", max_length=20, blank=True)
    driving_license_no = models.CharField(max_length=30, blank=True)
    driving_license_expiry = models.DateField(null=True, blank=True)
    psv_badge_no = models.CharField("PSV badge no.", max_length=30, blank=True)
    psv_badge_expiry = models.DateField(null=True, blank=True)
    guide_certification = models.CharField(max_length=80, blank=True, help_text="e.g. KPSGA Silver")
    guide_certification_expiry = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    manager = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="reports",
                                help_text="Who approves this person's leave")

    class Meta:
        ordering = ["full_name"]

    def __str__(self):
        return self.full_name

    def save(self, *args, **kwargs):
        self.phone = normalise_phone(self.phone)
        self.mpesa_number = normalise_phone(self.mpesa_number)
        if not self.initials:
            parts = self.full_name.split()
            self.initials = "".join(p[0] for p in parts[:2]).upper()
        super().save(*args, **kwargs)

    @property
    def is_available(self) -> bool:
        today = timezone.localdate()
        if self.leave_from and self.leave_to and self.leave_from <= today <= self.leave_to:
            return False
        return self.is_active and self.on_duty not in (self.OFF, self.ON_LEAVE)

    @property
    def short_name(self) -> str:
        parts = self.full_name.split()
        return f"{parts[0]} {parts[-1][0]}." if len(parts) > 1 else self.full_name

    @property
    def roles_label(self) -> str:
        bits = [b for b, f in (("Agent", self.is_sales_agent), ("Guide", self.is_guide), ("Driver", self.is_driver)) if f]
        return " · ".join(bits) or (self.title or "Staff")

    def get_absolute_url(self):
        from django.urls import reverse

        return reverse("people:detail", args=[self.pk])
