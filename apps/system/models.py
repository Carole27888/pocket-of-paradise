import json
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.fields import EncryptedCharField


class AuditLog(models.Model):
    """Discrete system actions (role changed, key saved, data imported, record anonymised…): the System Logs feed.

    Field-level history of business records lives in django-simple-history; this is the 'who did what' feed.
    """

    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    user_label = models.CharField(max_length=160, blank=True)
    action = models.CharField(max_length=40, db_index=True)
    target_type = models.CharField(max_length=60, blank=True)
    target_id = models.CharField(max_length=40, blank=True)
    target_label = models.CharField(max_length=200, blank=True)
    detail = models.TextField(blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-at", "-pk"]

    def __str__(self):
        return f"{self.action} · {self.target_label}"


class IntegrationCredential(models.Model):
    """One encrypted row per provider, edited from the Integration Keys screen. A saved key takes effect immediately."""

    provider = models.SlugField(max_length=30, unique=True)
    payload = EncryptedCharField(blank=True, help_text="JSON of field → secret value (encrypted at rest)")
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")
    updated_at = models.DateTimeField(default=timezone.now)
    last_test_at = models.DateTimeField(null=True, blank=True)
    last_test_ok = models.BooleanField(null=True)
    last_test_message = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["provider"]

    def __str__(self):
        return self.provider

    @property
    def values(self) -> dict:
        try:
            return json.loads(self.payload or "{}")
        except ValueError:
            return {}


class TodoItem(models.Model):
    """Private to its owner: not visible to managers, the CEO or Admin."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="todos")
    text = models.CharField(max_length=240)
    due = models.DateField(null=True, blank=True)
    done_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["done_at", "due", "-created_at"]

    def __str__(self):
        return self.text


class WeeklyPlanItem(models.Model):
    """One thing a person plans to do on a given day. Everyone plans their week and marks each day's items done,
    partly done or not done. Unlike the private to-do list, managers can see how plans are going."""

    PLANNED, DONE, PARTIAL, MISSED = "planned", "done", "partial", "missed"
    STATUSES = [(PLANNED, "Planned"), (DONE, "Done"), (PARTIAL, "Partly done"), (MISSED, "Not done")]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="plan_items")
    day = models.DateField(db_index=True)
    title = models.CharField(max_length=200)
    start_time = models.TimeField(null=True, blank=True)
    status = models.CharField(max_length=8, choices=STATUSES, default=PLANNED, db_index=True)
    note = models.CharField("How far did you get?", max_length=300, blank=True)
    marked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["day", "start_time", "pk"]
        indexes = [models.Index(fields=["user", "day"])]

    def __str__(self):
        return f"{self.day} {self.title}"

    @staticmethod
    def monday(d):
        return d - timedelta(days=d.weekday())


class ImportBatch(models.Model):
    """One bulk import: upload → checked (validated, nothing saved) → imported → (undone while still fresh)."""

    CHECKED, IMPORTED, UNDONE, DISCARDED = "checked", "imported", "undone", "discarded"
    STATUSES = [(CHECKED, "Checked"), (IMPORTED, "Imported"), (UNDONE, "Undone"), (DISCARDED, "Discarded")]
    kind = models.CharField(max_length=20, db_index=True)
    filename = models.CharField(max_length=200)
    status = models.CharField(max_length=10, choices=STATUSES, default=CHECKED, db_index=True)
    rows = models.JSONField(default=list)               # [{"n": 2, "clean": {...}, "errors": [...]}]
    total = models.PositiveIntegerField(default=0)
    valid = models.PositiveIntegerField(default=0)
    invalid = models.PositiveIntegerField(default=0)
    created = models.PositiveIntegerField(default=0)
    updated = models.PositiveIntegerField(default=0)
    skipped = models.PositiveIntegerField(default=0)
    failed = models.PositiveIntegerField(default=0)
    created_refs = models.JSONField(default=list)       # ["leads.Enquiry:12", ...] so undo deletes only what this batch made
    batch_errors = models.JSONField(default=list)       # problems with the file as a whole (e.g. opening balances don't balance)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(default=timezone.now)
    imported_at = models.DateTimeField(null=True, blank=True)
    undone_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.kind} · {self.filename}"

    UNDO_DAYS = 7

    @property
    def can_undo(self) -> bool:
        return self.status == self.IMPORTED and self.imported_at and self.imported_at > timezone.now() - __import__('datetime').timedelta(days=self.UNDO_DAYS)
