"""Agents email clients from their own mailbox; every email automatically copies the Director's chosen address.
The same account can be connected over IMAP to read the real inbox and drafts inside the system."""
from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.fields import EncryptedCharField


class Mailbox(models.Model):
    class Security(models.TextChoices):
        SSL = "ssl", "SSL (port 465, usual for cPanel mail)"
        STARTTLS = "starttls", "STARTTLS (port 587, e.g. Gmail, Outlook)"

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="mailbox")
    email = models.EmailField("your email address")
    display_name = models.CharField(max_length=120, help_text="Shown to clients, e.g. Sarah Wanjiku · Pocket of Paradise")
    smtp_host = models.CharField("outgoing mail server", max_length=200, help_text="cPanel: mail.yourdomain.co.ke · Gmail: smtp.gmail.com")
    smtp_port = models.PositiveIntegerField("port", default=465)
    security = models.CharField(max_length=10, choices=Security.choices, default=Security.SSL)
    username = models.CharField(max_length=200, blank=True, help_text="Usually your full email address (leave blank to use it)")
    password = EncryptedCharField(blank=True, help_text="Your email password. Gmail needs an app password.")
    bcc_self = models.BooleanField("keep a copy in my inbox", default=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    imap_host = models.CharField("incoming mail server", max_length=200, blank=True, help_text="cPanel: mail.yourdomain.co.ke · Gmail: imap.gmail.com")
    imap_port = models.PositiveIntegerField("IMAP port", default=993, blank=True)
    imap_security = models.CharField(max_length=10, choices=Security.choices, default=Security.SSL, blank=True)
    imap_verified_at = models.DateTimeField(null=True, blank=True)
    imap_last_error = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.email

    @property
    def is_ready(self) -> bool:
        return bool(self.email and self.smtp_host and self.password)

    @property
    def is_imap_ready(self) -> bool:
        return bool(self.email and self.imap_host and self.password)


class MailSettings(models.Model):
    """Single row. Management chooses the address every client email is copied to."""

    director_cc = models.EmailField("CC address", blank=True)
    cc_enabled = models.BooleanField("Copy this address on every client email", default=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "mail settings"

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def cc_list(self) -> list:
        return [self.director_cc] if self.cc_enabled and self.director_cc else []


class EmailTemplate(models.Model):
    name = models.CharField(max_length=80, unique=True)
    subject = models.CharField(max_length=200)
    body = models.TextField(help_text="Merge fields: {client_first_name} {client_name} {agent_name} {agent_phone} {destination} {reference} {company}")
    is_active = models.BooleanField(default=True)
    sort = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort", "name"]

    def __str__(self):
        return self.name


class SentEmail(models.Model):
    class Status(models.TextChoices):
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"

    enquiry = models.ForeignKey("leads.Enquiry", null=True, blank=True, on_delete=models.SET_NULL, related_name="emails")
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    from_email = models.EmailField()
    to = models.EmailField()
    cc = models.CharField(max_length=300, blank=True)
    subject = models.CharField(max_length=200)
    body = models.TextField()
    attachments = models.CharField(max_length=500, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.SENT)
    error = models.TextField(blank=True)
    sent_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-sent_at"]

    def __str__(self):
        return f"{self.subject} → {self.to}"
