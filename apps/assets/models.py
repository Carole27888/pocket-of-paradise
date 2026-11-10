from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.models import Sequence, TimeStampedModel


class FixedAsset(TimeStampedModel):
    CATEGORIES = [("vehicle", "Vehicle"), ("equipment", "Equipment"), ("computer", "Computers & phones"), ("furniture", "Furniture"), ("other", "Other")]
    ACTIVE, DISPOSED = "active", "disposed"

    tag = models.CharField(max_length=14, unique=True, blank=True)
    name = models.CharField(max_length=160)
    category = models.CharField(max_length=10, choices=CATEGORIES, default="equipment", db_index=True)
    purchase_date = models.DateField(default=timezone.localdate)
    cost = models.DecimalField(max_digits=14, decimal_places=2)
    residual_value = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    useful_life_months = models.PositiveSmallIntegerField(default=60)
    assigned_to = models.ForeignKey("people.Employee", null=True, blank=True, on_delete=models.SET_NULL, related_name="assets")
    location = models.CharField(max_length=80, blank=True)
    serial = models.CharField(max_length=80, blank=True)
    status = models.CharField(max_length=10, choices=[(ACTIVE, "In service"), (DISPOSED, "Disposed")], default=ACTIVE, db_index=True)
    disposed_on = models.DateField(null=True, blank=True)
    disposal_value = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["tag"]

    def __str__(self):
        return f"{self.tag} {self.name}"

    def save(self, *a, **kw):
        if not self.tag:
            self.tag = Sequence.next_number("fixed_asset", "FA-", 4)
        super().save(*a, **kw)

    @property
    def monthly_depreciation(self) -> Decimal:
        if not self.useful_life_months:
            return Decimal("0")
        return ((self.cost - self.residual_value) / self.useful_life_months).quantize(Decimal("0.01"))

    def months_elapsed(self, as_of=None) -> int:
        as_of = as_of or timezone.localdate()
        end = self.disposed_on or as_of
        n = (end.year - self.purchase_date.year) * 12 + end.month - self.purchase_date.month
        return max(0, min(n, self.useful_life_months))

    def accumulated(self, as_of=None) -> Decimal:
        return min(self.monthly_depreciation * self.months_elapsed(as_of), self.cost - self.residual_value)

    def book_value(self, as_of=None) -> Decimal:
        return self.cost - self.accumulated(as_of)


class DepreciationRun(models.Model):
    """One posting per month: idempotent via the unique period."""

    period = models.CharField(max_length=7, unique=True)      # YYYY-MM
    amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    posted_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-period"]
