from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils import timezone

from apps.core.models import TimeStampedModel

DEPARTMENTS = [("all", "Everyone"), ("sales", "Sales"), ("frontdesk", "Front Desk"), ("trips", "Trips & Operations"),
               ("finance", "Finance"), ("marketing", "Marketing"), ("production", "Production"), ("hr", "People & Leave"),
               ("executive", "Executive")]


class CalendarEvent(TimeStampedModel):
    MEETING, LEAVE, TASK, OTHER, BOOKING = "meeting", "leave", "task", "other", "booking"
    KINDS = [(MEETING, "Meeting"), (TASK, "Task / deadline"), (BOOKING, "Booked slot"), (LEAVE, "Leave"), (OTHER, "Other")]

    title = models.CharField(max_length=160)
    start = models.DateTimeField(db_index=True)
    end = models.DateTimeField(null=True, blank=True)
    all_day = models.BooleanField(default=False)
    department = models.CharField(max_length=12, choices=DEPARTMENTS, default="all", db_index=True)
    kind = models.CharField(max_length=8, choices=KINDS, default=MEETING)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="calendar_events")
    location = models.CharField(max_length=120, blank=True)
    notes = models.TextField(blank=True)
    source = models.CharField(max_length=40, blank=True, db_index=True, help_text="Set for events created by other modules, e.g. leave:12")

    class Meta:
        ordering = ["start"]

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse("schedule:event", args=[self.pk])

    @property
    def local_date(self):
        return timezone.localtime(self.start).date()


class EventInvite(models.Model):
    PENDING, ACCEPTED, DECLINED = "pending", "accepted", "declined"
    STATUSES = [(PENDING, "Pending"), (ACCEPTED, "Accepted"), (DECLINED, "Declined")]
    event = models.ForeignKey(CalendarEvent, on_delete=models.CASCADE, related_name="invites")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="invites")
    status = models.CharField(max_length=9, choices=STATUSES, default=PENDING, db_index=True)
    invited_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    responded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = [("event", "user")]
        ordering = ["status", "event__start"]
