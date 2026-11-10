"""Work with an owner and a deadline: everyday tasks and customer complaints.
Overdue work escalates by itself (services.escalate_overdue): first to the owner's manager, then to the executives."""
from django.conf import settings
from django.db import models
from django.utils import timezone


class Task(models.Model):
    class Kind(models.TextChoices):
        TASK = "task", "Task"
        COMPLAINT = "complaint", "Customer complaint"

    class Priority(models.TextChoices):
        NORMAL = "normal", "Normal"
        HIGH = "high", "High"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        DONE = "done", "Done"
        CANCELLED = "cancelled", "Cancelled"

    kind = models.CharField(max_length=12, choices=Kind.choices, default=Kind.TASK, db_index=True)
    title = models.CharField(max_length=200)
    details = models.TextField(blank=True)
    priority = models.CharField(max_length=6, choices=Priority.choices, default=Priority.NORMAL)
    owner = models.ForeignKey("people.Employee", on_delete=models.PROTECT, related_name="tasks")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    due_at = models.DateTimeField(db_index=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN, db_index=True)
    done_at = models.DateTimeField(null=True, blank=True)
    done_note = models.TextField(blank=True)
    enquiry = models.ForeignKey("leads.Enquiry", null=True, blank=True, on_delete=models.SET_NULL, related_name="tasks")
    escalation_level = models.PositiveSmallIntegerField(default=0, help_text="0 none · 1 manager · 2 executives")
    escalated_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["status", "due_at"]

    def __str__(self):
        return self.title

    @property
    def is_overdue(self) -> bool:
        return self.status == self.Status.OPEN and self.due_at < timezone.now()
