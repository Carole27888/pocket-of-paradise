from django.db import models
from django.urls import reverse

from apps.core.fields import EncryptedCharField
from apps.core.models import SoftDeleteModel, TimeStampedModel
from apps.core.phone import is_kenyan, normalise_phone

KENYA_NAMES = {"kenya", "kenyan", "ke"}


class Client(SoftDeleteModel):
    """One record per real person / lead traveller."""

    full_name = models.CharField(max_length=120, db_index=True)
    phone = models.CharField(max_length=20, blank=True, db_index=True)
    alt_phone = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True, db_index=True)
    nationality = models.CharField(max_length=60, blank=True, default="Kenyan")
    country_of_residence = models.CharField(max_length=60, blank=True)
    passport_number = EncryptedCharField(blank=True)
    passport_hash = models.CharField(max_length=64, blank=True, db_index=True, editable=False)
    is_international = models.BooleanField(
        "Diaspora / international", default=False, help_text="Set automatically from phone and nationality."
    )
    preferred_language = models.CharField(max_length=30, blank=True, default="English")
    dietary_notes = models.CharField(max_length=200, blank=True)
    medical_notes = models.CharField(max_length=200, blank=True)
    notes = models.TextField(blank=True)
    marketing_opt_out = models.BooleanField("Do not send bulk messages", default=False, help_text="Set when the client asks to stop marketing messages.")
    owner = models.ForeignKey("people.Employee", null=True, blank=True, on_delete=models.SET_NULL,
                              related_name="owned_clients", help_text="The sales agent who looks after this client.")
    owner_assigned_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        ordering = ["full_name"]

    def __str__(self):
        return self.full_name

    def save(self, *args, **kwargs):
        import hashlib

        self.phone = normalise_phone(self.phone)
        self.alt_phone = normalise_phone(self.alt_phone)
        self.email = (self.email or "").strip().lower()
        if self.passport_number:
            norm = self.passport_number.replace(" ", "").upper()
            self.passport_hash = hashlib.sha256(norm.encode()).hexdigest()
        else:
            self.passport_hash = ""
        nat = (self.nationality or "").strip().lower()
        foreign_phone = bool(self.phone) and not is_kenyan(self.phone)
        foreign_nat = bool(nat) and nat not in KENYA_NAMES
        if foreign_phone or foreign_nat:
            self.is_international = True
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse("contacts:detail", args=[self.pk])

    @property
    def initials(self):
        return "".join(p[0] for p in self.full_name.split()[:2]).upper()

    def outbound_marker(self, key: str) -> bool:
        """Has an automated message tagged `key` already gone to this client? (prevents repeat sends)"""
        from apps.notifications.models import OutboundMessage

        return OutboundMessage.objects.filter(related=key, to=self.phone).exists()

    @property
    def masked_passport(self):
        p = self.passport_number or ""
        return f"••••{p[-3:]}" if len(p) > 3 else ("•••" if p else "")


class TravelPartyMember(TimeStampedModel):
    """Companion travelling with a lead client (attached to an enquiry/booking)."""

    RELATIONSHIPS = [("spouse", "Spouse / partner"), ("child", "Child"), ("family", "Family"), ("friend", "Friend"),
                     ("colleague", "Colleague"), ("other", "Other")]
    enquiry = models.ForeignKey("leads.Enquiry", on_delete=models.CASCADE, related_name="party_members")
    client = models.ForeignKey(Client, null=True, blank=True, on_delete=models.SET_NULL, related_name="trips_as_companion")
    full_name = models.CharField(max_length=120)
    relationship = models.CharField(max_length=10, choices=RELATIONSHIPS, default="family")
    is_child = models.BooleanField(default=False)
    age = models.PositiveSmallIntegerField(null=True, blank=True)
    passport_number = EncryptedCharField(blank=True)
    dietary_notes = models.CharField(max_length=200, blank=True)

    def __str__(self):
        return self.full_name
