"""The agents' assistant: briefings, nudges, end-of-day and weekly reports, and the managers' digest.

Everything is generated from data already in the system. It reminds, reports and asks; it never changes a record."""
from datetime import time

from django.conf import settings
from django.db import models
from django.utils import timezone


class CoachSettings(models.Model):
    """One row, set by the Sales Manager, COO or CEO (Sales → Assistant Settings)."""

    briefing_on = models.BooleanField("Morning briefing at first login", default=True)
    nudges_on = models.BooleanField("Nudges about pending work", default=True)
    end_of_day_on = models.BooleanField("End-of-day report", default=True)
    weekly_on = models.BooleanField("Monday weekly summary", default=True)
    digest_on = models.BooleanField("Daily digest to managers and the CEO", default=True)
    exec_briefing_on = models.BooleanField("Morning executive briefing to the CEO and COO", default=True)
    exec_briefing_at = models.TimeField("Executive briefing time", default=time(7, 30))
    briefing_at = models.TimeField("Send the morning briefing at (if the agent hasn't signed in yet)", default=time(7, 45))
    end_of_day_at = models.TimeField(default=time(18, 0))
    digest_at = models.TimeField(default=time(18, 30))
    weekly_at = models.TimeField("Weekly summary time (Mondays)", default=time(8, 0))
    work_starts = models.TimeField("Nudges start", default=time(8, 0))
    work_ends = models.TimeField("Nudges stop", default=time(19, 0))
    no_sundays = models.BooleanField("No nudges on Sundays", default=True)
    uncontacted_minutes = models.PositiveIntegerField("Nudge when a new lead is uncontacted for (minutes)", default=30)
    stale_lead_days = models.PositiveIntegerField("Nudge when an open lead has had no contact for (days)", default=3)
    stale_quote_days = models.PositiveIntegerField("Nudge when a sent quotation has no update for (days)", default=3)
    manager_alert_hours = models.PositiveIntegerField("Alert managers when a new lead is uncontacted for (hours)", default=2)
    max_nudges_per_day = models.PositiveIntegerField(default=8)
    send_email = models.BooleanField("Also send by e-mail", default=False)
    send_whatsapp = models.BooleanField("Also send briefings and reports by WhatsApp", default=False)
    use_ai = models.BooleanField("Word messages with AI (uses the AI key under Integration Keys; plain templates without it)", default=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "assistant settings"

    @classmethod
    def load(cls) -> "CoachSettings":
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class CoachMessage(models.Model):
    BRIEFING, NUDGE, EOD, WEEKLY, DIGEST, ALERT, EXEC = "briefing", "nudge", "eod", "weekly", "digest", "alert", "exec"
    KINDS = [(BRIEFING, "Morning briefing"), (NUDGE, "Nudge"), (EOD, "End-of-day report"), (WEEKLY, "Weekly summary"), (DIGEST, "Manager digest"), (ALERT, "Manager alert"), (EXEC, "Executive briefing")]

    employee = models.ForeignKey("people.Employee", on_delete=models.CASCADE, related_name="coach_messages")
    kind = models.CharField(max_length=10, choices=KINDS, db_index=True)
    key = models.CharField(max_length=120, help_text="One message per key per day (no repeats)")
    day = models.DateField(default=timezone.localdate, db_index=True)
    title = models.CharField(max_length=160)
    body = models.TextField()
    enquiry = models.ForeignKey("leads.Enquiry", null=True, blank=True, on_delete=models.SET_NULL, related_name="coach_messages")
    facts = models.JSONField(default=dict, blank=True)
    written_by_ai = models.BooleanField(default=False)
    sent_at = models.DateTimeField(default=timezone.now, db_index=True)
    read_at = models.DateTimeField(null=True, blank=True)
    reply = models.TextField(blank=True)
    replied_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-sent_at"]
        constraints = [models.UniqueConstraint(fields=["employee", "key", "day"], name="coach_once_per_key_per_day")]

    def __str__(self):
        return f"{self.employee} · {self.title}"
