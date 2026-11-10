from decimal import Decimal

from django.conf import settings
from django.db import models, transaction
from django.utils import timezone

from .middleware import get_current_user


class SoftDeleteQuerySet(models.QuerySet):
    def alive(self):
        return self.filter(deleted_at__isnull=True)


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+", editable=False
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+", editable=False
    )

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        user = get_current_user()
        if user is not None:
            if not self.pk and not self.created_by_id:
                self.created_by = user
            self.updated_by = user
        super().save(*args, **kwargs)


class SoftDeleteModel(TimeStampedModel):
    deleted_at = models.DateTimeField(null=True, blank=True, editable=False)

    objects = SoftDeleteQuerySet.as_manager()

    class Meta:
        abstract = True

    def soft_delete(self):
        self.deleted_at = timezone.now()
        self.save(update_fields=["deleted_at", "updated_at"])


class Sequence(models.Model):
    """Gap-free counters for reference numbers (ENQ-00001, QT-0001, INV-0001 ...).

    Always use `next_number()`; never derive numbers from MAX(id)+1.
    """

    name = models.CharField(max_length=40, unique=True)
    prefix = models.CharField(max_length=10)
    padding = models.PositiveSmallIntegerField(default=4)
    last_value = models.PositiveIntegerField(default=0)

    def __str__(self):
        return f"{self.prefix}-{self.last_value:0{self.padding}d}"

    @classmethod
    def next_number(cls, name: str, prefix: str | None = None, padding: int = 4) -> str:
        with transaction.atomic():
            seq, _ = cls.objects.get_or_create(
                name=name, defaults={"prefix": prefix or name.upper()[:6], "padding": padding}
            )
            seq = cls.objects.select_for_update().get(pk=seq.pk)
            seq.last_value += 1
            seq.save(update_fields=["last_value"])
            return f"{seq.prefix}-{seq.last_value:0{seq.padding}d}"


class Currency(models.Model):
    code = models.CharField(max_length=3, primary_key=True)
    name = models.CharField(max_length=40)
    symbol = models.CharField(max_length=6)
    is_base = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=10)

    class Meta:
        ordering = ["sort_order", "code"]
        verbose_name_plural = "currencies"

    def __str__(self):
        return self.code


class ExchangeRate(models.Model):
    """How many KES one unit of `currency` buys on `date`."""

    currency = models.ForeignKey(Currency, on_delete=models.CASCADE, related_name="rates")
    date = models.DateField(db_index=True)
    rate_to_base = models.DecimalField(max_digits=14, decimal_places=6)
    source = models.CharField(max_length=40, default="mock")

    class Meta:
        unique_together = [("currency", "date")]
        ordering = ["-date"]

    def __str__(self):
        return f"1 {self.currency_id} = {self.rate_to_base} KES ({self.date})"

    @classmethod
    def rate_for(cls, code: str, on_date=None) -> Decimal:
        if code == settings.BASE_CURRENCY:
            return Decimal("1")
        on_date = on_date or timezone.localdate()
        rate = cls.objects.filter(currency_id=code, date__lte=on_date).order_by("-date").first()
        if rate is None:
            rate = cls.objects.filter(currency_id=code).order_by("date").first()
        if rate is None:
            raise LookupError(f"No exchange rate stored for {code}")
        return rate.rate_to_base

    @classmethod
    def to_base(cls, amount: Decimal, code: str, on_date=None) -> Decimal:
        return (Decimal(amount) * cls.rate_for(code, on_date)).quantize(Decimal("0.01"))


DEFAULT_TERMS = (
    "Rates are per the itinerary shown and valid until the date above, subject to availability.\n"
    "A deposit is required to confirm the booking; the balance is due 30 days before travel.\n"
    "Cancellations: 30+ days before travel lose the deposit; later cancellations follow the supplier terms.\n"
    "Park fees, taxes and vehicle fuel are included only where listed. Travel insurance is recommended.\n"
    "Pay by M-PESA Paybill, bank transfer or card; payment details are on the invoice."
)


class CompanySetting(models.Model):
    """Single-row company profile used on PDFs, manifest and settings pages."""

    name = models.CharField(max_length=120, default="Pocket of Paradise Travel Company")
    standard_terms = models.TextField("Standard payment & cancellation terms", default=DEFAULT_TERMS, blank=True,
                                      help_text="Filled into every new itinerary and quotation. Operations keeps this up to date.")
    two_factor_enabled = models.BooleanField("Two-factor sign-in", default=False,
                                             help_text="Switched on or off by an administrator under Security Settings.")
    tagline = models.CharField(max_length=160, default="Travel Company")
    kra_pin = models.CharField("KRA PIN", max_length=20, blank=True)
    tra_license_no = models.CharField("TRA licence no.", max_length=40, blank=True)
    paybill = models.CharField("M-PESA Paybill", max_length=20, blank=True)
    phone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)
    website = models.URLField(blank=True)
    bank_details = models.TextField(blank=True, help_text="Printed on client itineraries. One 'Label: value' per line, e.g. 'Bank: UBA'.")
    payment_note = models.CharField(max_length=160, blank=True, help_text="Shown under the paybill on client itineraries")
    documents_list = models.TextField("Documents to bring", blank=True, help_text="One per line, shown on the client's trip page. Leave empty for the standard list.")
    packing_list = models.TextField("What to pack", blank=True, help_text="One per line, shown on the client's trip page. Leave empty for the standard list.")

    def __str__(self):
        return self.name

    @property
    def bank_rows(self):
        rows = []
        for line in self.bank_details.splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                rows.append((k.strip(), v.strip()))
        return rows

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


from .jobs.models import Job, JobLock, ScheduleState  # noqa: E402,F401
