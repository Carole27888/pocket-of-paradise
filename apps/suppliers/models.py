from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils import timezone
from simple_history.models import HistoricalRecords

from apps.core.models import Sequence, SoftDeleteModel, TimeStampedModel
from apps.core.phone import normalise_phone


class Supplier(SoftDeleteModel):
    KINDS = [("lodge", "Lodge / camp"), ("hotel", "Hotel / resort"), ("transport", "Transporter"),
             ("caterer", "Caterer"), ("activity", "Activity provider"), ("park", "Park-fee agent / authority"),
             ("airline", "Airline / charter"), ("other", "Other")]

    name = models.CharField(max_length=140, unique=True)
    kind = models.CharField(max_length=10, choices=KINDS, default="lodge", db_index=True)
    contact_name = models.CharField(max_length=100, blank=True)
    phone = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    location = models.CharField(max_length=120, blank=True)
    kra_pin = models.CharField("KRA PIN", max_length=20, blank=True)
    payment_terms_days = models.PositiveSmallIntegerField(default=30)
    bank_details = models.CharField(max_length=200, blank=True, help_text="Bank, account no. or M-PESA paybill/till")
    withholding_applies = models.BooleanField(
        "Withholding tax applies", default=False, help_text="Professional/contractor services (not lodges or parks).")
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        self.phone = normalise_phone(self.phone)
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse("suppliers:detail", args=[self.pk])

    @property
    def outstanding(self) -> Decimal:
        return sum((b.balance for b in self.bills.exclude(status__in=[Bill.VOID, Bill.PAID])), Decimal("0"))


class Bill(TimeStampedModel):
    AWAITING, APPROVED, SCHEDULED, PAID, VOID = "awaiting", "approved", "scheduled", "paid", "void"
    STATUSES = [(AWAITING, "Awaiting approval"), (APPROVED, "Approved"), (SCHEDULED, "Scheduled"),
                (PAID, "Paid"), (VOID, "Void")]
    CATEGORIES = [("park_fees", "Park fees"), ("accommodation", "Accommodation"), ("transport", "Transport"),
                  ("catering", "Catering"), ("activities", "Activities"), ("professional", "Professional fees"),
                  ("other", "Other")]

    number = models.CharField(max_length=16, unique=True, blank=True)
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="bills")
    departure = models.ForeignKey("trips.Departure", null=True, blank=True, on_delete=models.SET_NULL,
                                  related_name="bills")
    supplier_ref = models.CharField("Supplier invoice no.", max_length=60, blank=True)
    category = models.CharField(max_length=14, choices=CATEGORIES, default="accommodation")
    description = models.CharField(max_length=200, blank=True)
    bill_date = models.DateField(default=timezone.localdate)
    due_date = models.DateField(db_index=True)
    currency = models.CharField(max_length=3, default="KES")
    fx_rate = models.DecimalField(max_digits=14, decimal_places=6, default=1)
    amount = models.DecimalField("Amount (incl. VAT)", max_digits=14, decimal_places=2)
    vat_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    wht_amount = models.DecimalField("Withholding tax", max_digits=14, decimal_places=2, default=0)
    prepayment = models.BooleanField(default=False, help_text="Paid before the trip (deposit to lodge / park fees).")
    status = models.CharField(max_length=10, choices=STATUSES, default=AWAITING, db_index=True)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+")
    approved_at = models.DateTimeField(null=True, blank=True)
    scheduled_for = models.DateField(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    history = HistoricalRecords()

    class Meta:
        ordering = ["due_date", "pk"]
        indexes = [models.Index(fields=["status", "due_date"])]

    def __str__(self):
        return f"{self.number} · {self.supplier}"

    def save(self, *args, **kwargs):
        if not self.number:
            self.number = Sequence.next_number("bill", "BILL", 4)
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse("suppliers:bill", args=[self.pk])

    @property
    def net_amount(self) -> Decimal:
        return self.amount - self.vat_amount

    @property
    def payable_amount(self) -> Decimal:
        """What we actually pay the supplier (gross less any tax withheld on their behalf)."""
        return self.amount - self.wht_amount

    @property
    def paid_amount(self) -> Decimal:
        if not hasattr(self, "_paid"):
            self._paid = sum((p.amount for p in self.payments.all()), Decimal("0"))
        return self._paid

    @property
    def balance(self) -> Decimal:
        return max(self.payable_amount - self.paid_amount, Decimal("0"))

    @property
    def is_overdue(self) -> bool:
        return self.status in (self.APPROVED, self.SCHEDULED, self.AWAITING) and self.due_date < timezone.localdate()

    @property
    def due_label(self) -> str:
        if self.status == self.PAID:
            return "Paid"
        if self.status == self.VOID:
            return "Void"
        if self.is_overdue:
            return "Overdue"
        if self.due_date <= timezone.localdate() + timedelta(days=7):
            return "Due this week"
        return self.get_status_display()

    @property
    def amount_kes(self) -> Decimal:
        return (self.amount * self.fx_rate).quantize(Decimal("0.01"))


class BillPayment(models.Model):
    METHODS = [("bank", "Bank transfer / RTGS"), ("mpesa", "M-PESA"), ("cheque", "Cheque"), ("cash", "Cash"),
               ("card", "Card")]
    bill = models.ForeignKey(Bill, on_delete=models.CASCADE, related_name="payments")
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    method = models.CharField(max_length=8, choices=METHODS, default="bank")
    reference = models.CharField(max_length=60, blank=True)
    paid_at = models.DateTimeField(default=timezone.now)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+")

    class Meta:
        ordering = ["-paid_at", "-pk"]

    def __str__(self):
        return f"{self.bill.number} · {self.amount}"


# ---- computed columns used by the list screens
Supplier.terms_label = property(lambda self: f"{self.payment_terms_days} days")


def _departure_cost_properties():
    from apps.trips.models import Departure

    live = lambda d: [b for b in d.bills.all() if b.status != "void"]  # noqa: E731
    Departure.bill_count = property(lambda self: len(live(self)))
    Departure.billed_total = property(lambda self: sum((b.amount * b.fx_rate for b in live(self)), Decimal("0")))
    Departure.owed_total = property(lambda self: sum((b.balance * b.fx_rate for b in live(self) if b.status != "paid"), Decimal("0")))
    Departure.prepay_label = property(lambda self: "Yes" if any(b.prepayment for b in live(self)) else "No")


_departure_cost_properties()
