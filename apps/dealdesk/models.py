import secrets
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone
from simple_history.models import HistoricalRecords

from apps.core.fields import EncryptedCharField
from apps.core.models import Sequence, TimeStampedModel


class ClientKYC(TimeStampedModel):
    """Identity check on a client. Can be made a precondition of taking a deposit (SalesSettings.require_kyc_for_deposit)."""

    PENDING, VERIFIED, REJECTED = "pending", "verified", "rejected"
    STATUSES = [(PENDING, "Pending"), (VERIFIED, "Verified"), (REJECTED, "Rejected")]
    ID_TYPES = [("national_id", "National ID"), ("passport", "Passport"), ("alien_id", "Alien / resident ID"), ("other", "Other")]

    client = models.OneToOneField("contacts.Client", on_delete=models.CASCADE, related_name="kyc")
    id_type = models.CharField(max_length=12, choices=ID_TYPES, default="national_id")
    id_number = EncryptedCharField(blank=True)
    document = models.FileField(upload_to="kyc/%Y/%m/", blank=True)
    status = models.CharField(max_length=10, choices=STATUSES, default=PENDING, db_index=True)
    verified_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    verified_at = models.DateTimeField(null=True, blank=True)
    note = models.CharField(max_length=240, blank=True)
    history = HistoricalRecords()

    class Meta:
        verbose_name = "client KYC"

    def __str__(self):
        return f"KYC · {self.client}"


class SignatureRequest(TimeStampedModel):
    """A quotation or booking sent to the client for an electronic signature (typed name + drawn signature, IP and time recorded)."""

    PENDING, SIGNED, DECLINED = "pending", "signed", "declined"
    STATUSES = [(PENDING, "Awaiting signature"), (SIGNED, "Signed"), (DECLINED, "Declined")]

    token = models.CharField(max_length=40, unique=True, editable=False)
    title = models.CharField(max_length=160)
    quotation = models.ForeignKey("sales.Quotation", null=True, blank=True, on_delete=models.CASCADE, related_name="signatures")
    booking = models.ForeignKey("sales.Booking", null=True, blank=True, on_delete=models.CASCADE, related_name="signatures")
    client = models.ForeignKey("contacts.Client", on_delete=models.CASCADE, related_name="signatures")
    status = models.CharField(max_length=10, choices=STATUSES, default=PENDING, db_index=True)
    sent_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    signed_name = models.CharField(max_length=120, blank=True)
    signature_image = models.TextField(blank=True, help_text="Base64 PNG from the signing pad")
    signed_at = models.DateTimeField(null=True, blank=True)
    signed_ip = models.GenericIPAddressField(null=True, blank=True)
    content_hash = models.CharField(max_length=64, blank=True, help_text="SHA-256 of the document text at the time it was sent")
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title

    def save(self, *a, **kw):
        if not self.token:
            self.token = secrets.token_urlsafe(24)
        super().save(*a, **kw)

    @property
    def expired(self):
        return self.status == self.PENDING and self.expires_at and self.expires_at < timezone.now()


class PaymentPlan(TimeStampedModel):
    booking = models.OneToOneField("sales.Booking", on_delete=models.CASCADE, related_name="plan")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    note = models.CharField(max_length=200, blank=True)

    def __str__(self):
        return f"Plan · {self.booking.reference}"

    @property
    def total(self):
        return sum((i.amount for i in self.instalments.all()), Decimal("0"))


class Instalment(models.Model):
    plan = models.ForeignKey(PaymentPlan, on_delete=models.CASCADE, related_name="instalments")
    due_date = models.DateField(db_index=True)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    paid_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    reminded_on = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["due_date", "pk"]

    @property
    def outstanding(self):
        return max(self.amount - self.paid_amount, Decimal("0"))

    @property
    def state(self):
        if self.outstanding <= 0:
            return "paid"
        if self.due_date < timezone.localdate():
            return "overdue"
        return "upcoming"


class BudgetRequest(TimeStampedModel):
    PENDING, APPROVED, REJECTED = "pending", "approved", "rejected"
    STATUSES = [(PENDING, "Pending"), (APPROVED, "Approved"), (REJECTED, "Rejected")]

    reference = models.CharField(max_length=14, unique=True, blank=True)
    title = models.CharField(max_length=160)
    department = models.CharField(max_length=60, blank=True)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    reason = models.TextField(blank=True)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="budget_requests")
    status = models.CharField(max_length=10, choices=STATUSES, default=PENDING, db_index=True)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.CharField(max_length=240, blank=True)
    history = HistoricalRecords()

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.reference

    def save(self, *a, **kw):
        if not self.reference:
            self.reference = Sequence.next_number("budget_request", "BR-", 4)
        super().save(*a, **kw)
