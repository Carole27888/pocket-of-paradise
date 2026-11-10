from django.conf import settings
from django.db import models
from django.utils import timezone


class ScheduledReport(models.Model):
    DAILY, WEEKLY, MONTHLY = "daily", "weekly", "monthly"
    FREQ = [(DAILY, "Every day"), (WEEKLY, "Every Monday"), (MONTHLY, "1st of the month")]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="scheduled_reports")
    report_key = models.CharField(max_length=40)
    frequency = models.CharField(max_length=8, choices=FREQ, default=WEEKLY)
    range_days = models.PositiveSmallIntegerField(default=30, help_text="How many days back each report covers")
    active = models.BooleanField(default=True)
    last_run_at = models.DateTimeField(null=True, blank=True)
    last_status = models.CharField(max_length=160, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["report_key"]
        constraints = [models.UniqueConstraint(fields=["user", "report_key"], name="one_schedule_per_report_per_user")]
