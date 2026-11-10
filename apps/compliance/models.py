from datetime import timedelta

from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils import timezone
from simple_history.models import HistoricalRecords

from apps.core.models import Sequence, TimeStampedModel

DUE_DAYS = 30


class DataRequest(TimeStampedModel):
    ACCESS, CORRECTION, DELETION, OBJECTION = "access", "correction", "deletion", "objection"
    KINDS = [(ACCESS, "Access (copy of my data)"), (CORRECTION, "Correction"), (DELETION, "Deletion / erasure"),
             (OBJECTION, "Objection to processing")]
    OPEN, PROGRESS, CLOSED = "open", "in_progress", "closed"
    STATUSES = [(OPEN, "Open"), (PROGRESS, "In progress"), (CLOSED, "Closed")]

    reference = models.CharField(max_length=16, unique=True, blank=True)
    kind = models.CharField(max_length=12, choices=KINDS, default=ACCESS, db_index=True)
    client = models.ForeignKey("contacts.Client", null=True, blank=True, on_delete=models.SET_NULL, related_name="data_requests")
    requester_name = models.CharField(max_length=120)
    requester_email = models.EmailField(blank=True)
    requester_phone = models.CharField(max_length=30, blank=True)
    details = models.TextField(blank=True, help_text="What the person asked for")
    received_on = models.DateField(default=timezone.localdate)
    due_on = models.DateField(db_index=True)
    status = models.CharField(max_length=12, choices=STATUSES, default=OPEN, db_index=True)
    identity_confirmed = models.BooleanField(default=False)
    identity_note = models.CharField(max_length=200, blank=True, help_text="How identity was confirmed (e.g. matched phone + passport last 3)")
    response = models.TextField(blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    closed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    anonymised_at = models.DateTimeField(null=True, blank=True)

    history = HistoricalRecords()

    class Meta:
        ordering = ["status", "due_on"]

    def __str__(self):
        return f"{self.reference} · {self.requester_name}"

    def save(self, *args, **kwargs):
        if not self.reference:
            self.reference = Sequence.next_number("data_request", "DSR", 4)
        if not self.due_on:
            self.due_on = self.received_on + timedelta(days=DUE_DAYS)
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse("compliance:request", args=[self.pk])

    @property
    def is_overdue(self) -> bool:
        return self.status != self.CLOSED and self.due_on < timezone.localdate()

    @property
    def days_left(self) -> int:
        return (self.due_on - timezone.localdate()).days


class DataBreach(TimeStampedModel):
    SEVERITY = [("low", "Low"), ("medium", "Medium"), ("high", "High")]
    reference = models.CharField(max_length=16, unique=True, blank=True)
    title = models.CharField(max_length=160)
    what_happened = models.TextField()
    data_affected = models.CharField(max_length=240, help_text="e.g. names and phone numbers of 40 guests")
    people_affected = models.PositiveIntegerField(default=0)
    severity = models.CharField(max_length=6, choices=SEVERITY, default="medium")
    detected_at = models.DateTimeField(default=timezone.now)
    contained_at = models.DateTimeField(null=True, blank=True)
    reported_at = models.DateTimeField(null=True, blank=True, help_text="Reported to the Data Protection Commissioner (ODPC)")
    notified_at = models.DateTimeField(null=True, blank=True, help_text="Affected people told")
    actions_taken = models.TextField(blank=True)

    history = HistoricalRecords()

    class Meta:
        ordering = ["-detected_at"]

    def __str__(self):
        return f"{self.reference} · {self.title}"

    def save(self, *args, **kwargs):
        if not self.reference:
            self.reference = Sequence.next_number("data_breach", "BR", 3)
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse("compliance:breach", args=[self.pk])

    @property
    def status(self) -> str:
        if self.contained_at and (self.reported_at or self.severity == "low") and (self.notified_at or self.severity != "high"):
            return "Closed"
        return "Contained" if self.contained_at else "Open"

    @property
    def report_deadline(self):
        return self.detected_at + timedelta(hours=72)

    @property
    def report_overdue(self) -> bool:
        return self.severity != "low" and not self.reported_at and timezone.now() > self.report_deadline


class RetentionRun(models.Model):
    ran_at = models.DateTimeField(default=timezone.now)
    by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    counts = models.JSONField(default=dict)

    class Meta:
        ordering = ["-ran_at"]
